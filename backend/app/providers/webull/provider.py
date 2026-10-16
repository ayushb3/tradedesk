"""Read-only Webull OpenAPI adapter behind the DataProvider protocol.

Design constraints (M5):

  * **Read-only.** No order-entry methods exist here, by design.
  * **Lazy SDK.** The official Webull OpenAPI SDK is imported *inside* the
    methods that need it, so importing this module (and running ``mode=mock``)
    never requires the SDK. If the SDK is absent, a live call raises
    :class:`WebullSDKNotInstalled` with install instructions.
  * **Graceful entitlement/auth handling.** A 403 from the data layer becomes
    :class:`WebullSubscriptionError` (OpenAPI Quotes Subscription required); a
    401 becomes :class:`WebullAuthError`. The app never crashes on these.
  * **Token pre-flight.** Before any SDK call, ``_ensure_token()`` checks that
    ``<token_dir>/token.txt`` exists and has status == NORMAL and is not
    expired. If not, :class:`WebullTokenPending` is raised with an actionable
    message pointing at ``backend/scripts/webull_auth.py``.

Uses ``webull-openapi-python-sdk`` (one package, Python 3.8–3.13). The old
``webull-python-sdk-{core,mdata,trade}`` packages are deprecated and crash on
Python 3.12+ due to vendored urllib3 incompatibilities.

The response->model mapping lives in :mod:`.mappers` (the unit-tested seam).
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from datetime import time as dtime
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Generator

from app.config import Settings
from app.models.market import Account, Candle, OptionContract, Position
from app.models.trades import Fill

from . import mappers
from .errors import (
    WebullAuthError,
    WebullConfigError,
    WebullSDKNotInstalled,
    WebullSubscriptionError,
    WebullTokenPending,
)

UTC = timezone.utc

# Our timeframe tokens -> Webull bar interval tokens.
# New SDK (webull-openapi-python-sdk) uses uppercase Timespan enum names:
# Timespan.M1.name == "M1", Timespan.M5.name == "M5", etc.
_TIMEFRAME_TO_WEBULL = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "M60", "1d": "D"}

# Webull's HTTP Data API supports start_time/end_time (epoch ms) in addition to
# a count cap. We pass all three so that bars from any date in history are
# retrievable, not just the most-recent N bars.
# SDK docstring says max count is 1200; live API has accepted up to 1650 but
# may reject larger values with "count ranges 1 to 1650". 1200 is safe and
# comfortably covers one full day of 1-min bars (including extended hours).
_MAX_BARS = 1200

# Order-history pagination. get_order_history defaults to page_size=10 and
# (with no dates) the last 7 days only, so we page explicitly. The live US API
# caps page_size at 100 ("value should be between 10 and 100", HTTP 417) — the
# SDK docstring only documents the default, not the max, so this was verified
# against the live endpoint. _MAX_ORDER_PAGES caps the walk so a non-advancing
# cursor can never loop forever.
_ORDER_PAGE_SIZE = 100
_MAX_ORDER_PAGES = 500
# Bounded lookback horizon when no `since` is supplied.
# The live API rejects start_date values older than ~730 days with HTTP 417
# ("OAUTH_OPENAPI_PARAM_ERR: invalid start_date"). We walk backward in
# 90-day chunks starting from today so we never send a start_date the API
# rejects, and cover the ~2-year history that the API makes available.
_HISTORY_LOOKBACK_DAYS = 730  # how far back we look by default
_DATE_WINDOW_DAYS = 90        # chunk size; API tolerates ≤90-day windows reliably
# Rate limit: order-history endpoint is 2 req/2 s (≈1 req/s sustained). The SDK
# also issues its own un-throttled /openapi/config verification when a client is
# built, which counts against the same budget. All Webull SDK calls are spaced
# by the process-wide `_RATE_GATE` below (`_GATE_MIN_INTERVAL_S` interval).
# On HTTP 429, retry the same call up to this many times with exponential
# backoff (starting at `_GATE_MIN_INTERVAL_S`, doubling) before giving up and
# surfacing the error.
_RATE_LIMIT_RETRIES = 4

# Webull order-history is capped at 2 req/2 s (≈1 req/s sustained). Crucially,
# the SDK fires its OWN un-throttled /openapi/config verification alongside each
# of our calls, so one gated call = ~2 actual HTTP hits. A 1.1 s interval emits
# ~1.8 hits/s and still trips 429 (verified live 2026-07-13: ~2 429s/s across a
# full-history walk, all absorbed by retry but noisy). 2.2 s keeps our call +
# the SDK shadow call at ~0.9 hits/s — comfortably under budget.
_GATE_MIN_INTERVAL_S = 2.2


class _RateGate:
    """Process-wide min-interval limiter shared by all provider instances.

    Every Webull SDK call routes through WebullProvider._guard, which acquires
    this gate first — so the request rate is structurally bounded regardless of
    which endpoint or how many concurrent requests. Thread-safe: FastAPI runs
    sync handlers in a threadpool.
    """

    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next = now + self._min


_RATE_GATE = _RateGate(_GATE_MIN_INTERVAL_S)

# Default token directory, resolved relative to this file's repo root so it's
# always absolute regardless of cwd. Matches the default in webull_auth.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_DEFAULT_TOKEN_DIR = _REPO_ROOT / "data" / "webull-token"


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _utc_today() -> date:
    """Today's date in UTC (used as the order-history window end)."""
    return datetime.now(UTC).date()


def _date_windows(
    horizon: date, today: date, chunk_days: int
) -> Generator[tuple[date, date], None, None]:
    """Yield (start, end) date pairs covering [horizon, today] in descending order.

    Walks backward from ``today`` in ``chunk_days``-sized chunks, so the most
    recent data is fetched first and every chunk's start_date stays within the
    bounded range the live API accepts.  The final chunk may be shorter than
    ``chunk_days`` if ``horizon`` falls mid-window.

    Example with chunk_days=90, today=2026-07-10, horizon=2026-04-11:
        (2026-04-11, 2026-07-10)   ← single 90-day chunk

    Example with chunk_days=90, today=2026-07-10, horizon=2024-07-11:
        (2026-04-11, 2026-07-10)
        (2026-01-11, 2026-04-10)
        …
        (2024-07-11, 2024-10-09)
    """
    window_end = today
    while window_end >= horizon:
        window_start = max(horizon, window_end - timedelta(days=chunk_days - 1))
        yield window_start, window_end
        if window_start <= horizon:
            break
        window_end = window_start - timedelta(days=1)


class WebullProvider:
    """Live, read-only Webull data provider."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        key = getattr(settings, "webull_app_key", "") or ""
        secret = getattr(settings, "webull_app_secret", "") or ""
        missing = [
            name
            for name, val in (("app_key", key), ("app_secret", secret))
            if not str(val).strip()
        ]
        if missing:
            # Never echo secret values — only which names are absent.
            raise WebullConfigError(
                "Webull credentials missing: "
                + ", ".join(missing)
                + ". Set WEBULL_APP_KEY and WEBULL_APP_SECRET in your .env "
                "(or switch TRADEDESK_MODE=mock)."
            )
        self.region = getattr(settings, "webull_region", "us") or "us"
        self._app_key = key
        self._app_secret = secret

        # Resolve token directory: settings > env var (already merged by
        # get_settings()) > default <repo_root>/data/webull-token/.
        raw_token_dir = getattr(settings, "webull_token_dir", "") or ""
        if raw_token_dir.strip():
            self._token_dir: Path = Path(raw_token_dir.strip()).expanduser().resolve()
        else:
            self._token_dir = _DEFAULT_TOKEN_DIR.resolve()

        # Lazily-built SDK handles; kept private so tests can inject fakes.
        self._api_client: Any = None
        self._mdata: Any = None
        self._trade: Any = None
        self._account_id: str | None = None

    # ------------------------------------------------------------------ #
    # Token pre-flight
    # ------------------------------------------------------------------ #
    def _ensure_token(self) -> None:
        """Raise :class:`WebullTokenPending` if the token file is absent,
        has a non-NORMAL status, or has expired.

        Called from ``_clients()`` before the SDK clients are built so every
        live call fails fast with an actionable message if auth isn't ready.

        Token file format (written by webull_auth.py and the SDK's
        TokenManager.save_token_to_local):
            line 1: token string
            line 2: expires epoch-milliseconds (int)
            line 3: status  (NORMAL | PENDING | INVALID | EXPIRED)
        """
        token_file = self._token_dir / "token.txt"
        _pending_msg = (
            "Run `python backend/scripts/webull_auth.py` and verify via the "
            "Webull mobile app (Messages → OpenAPI Notifications → Check Now "
            "→ enter SMS code). "
            f"Token dir: {self._token_dir}."
        )

        if not token_file.exists():
            raise WebullTokenPending(_pending_msg)

        try:
            lines = token_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise WebullTokenPending(_pending_msg) from exc

        # Parse status (line 3, 0-indexed line 2).
        status = lines[2].strip() if len(lines) > 2 else ""
        if status != "NORMAL":
            raise WebullTokenPending(_pending_msg)

        # Parse expiry (line 2, 0-indexed line 1).
        expires_str = lines[1].strip() if len(lines) > 1 else "0"
        try:
            expires_ms = int(expires_str)
        except ValueError:
            expires_ms = 0

        if expires_ms > 0:
            now_ms = int(time.time() * 1000)
            if now_ms >= expires_ms:
                raise WebullTokenPending(_pending_msg)

        # Token is valid — set it on the (already-built or about-to-be-built)
        # ApiClient so it goes into the x-access-token header.
        token_str = lines[0].strip() if lines else ""
        if self._api_client is not None and token_str and hasattr(self._api_client, "set_token"):
            self._api_client.set_token(token_str)

    # ------------------------------------------------------------------ #
    # SDK plumbing (lazy)
    # ------------------------------------------------------------------ #
    def _clients(self) -> tuple[Any, Any]:
        """Build (and cache) the market-data + trade SDK clients on first use."""
        if self._api_client is None:
            try:
                # New unified package: webull-openapi-python-sdk (one package).
                # Replaces deprecated webull-python-sdk-{core,mdata,trade}.
                from webull.core.client import ApiClient  # type: ignore
                from webull.data.data_client import DataClient  # type: ignore
                from webull.trade.trade_client import TradeClient  # type: ignore
            except ImportError as exc:  # SDK not installed -> actionable error.
                raise WebullSDKNotInstalled(
                    "The Webull OpenAPI SDK is not installed. Install it with: "
                    "pip install webull-openapi-python-sdk"
                ) from exc
            client = ApiClient(self._app_key, self._app_secret, self.region)
            # Point the SDK at the correct token directory (absolute path).
            client.set_token_dir(str(self._token_dir))
            self._api_client = client
            self._mdata = DataClient(client)
            self._trade = TradeClient(client)

        # Pre-flight: verify token is present, NORMAL, and not expired.
        # Called every time _clients() is called so a mid-session expiry is
        # caught on the next request rather than producing a cryptic 401.
        self._ensure_token()

        return self._mdata, self._trade

    def _guard(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an SDK call through the process-wide rate gate, translating
        transport failures into typed errors.

        Handles two shapes: (a) the SDK raises an exception carrying an HTTP
        status, and (b) the SDK returns a response object whose status_code is
        an error. Both are inspected for 401/403.

        Every call is preceded by ``_RATE_GATE.acquire()``, so all Webull SDK
        traffic — regardless of endpoint — is structurally spaced out. On a
        rate-limit rejection (``_RateLimitError``, raised by ``_translate`` /
        ``_translate_status`` for 429 / rate-limit response bodies) this
        retries with exponential backoff up to ``_RATE_LIMIT_RETRIES`` times.
        All other typed errors propagate immediately without retry.
        """
        delay = _GATE_MIN_INTERVAL_S
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            _RATE_GATE.acquire()
            try:
                try:
                    resp = fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - translate everything to typed
                    self._translate(exc)
                    raise  # unreachable when _translate raises; kept for type-checkers
                status = _http_status(resp)
                if status is not None and status >= 400:
                    self._translate_status(status, _payload(resp))
            except _RateLimitError:
                if attempt >= _RATE_LIMIT_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2  # exponential backoff
                continue
            return _payload(resp)

    def _translate(self, exc: Exception) -> None:
        status = _http_status(exc)
        if status is not None:
            self._translate_status(status, str(exc))
        # No status we recognize: re-raise as a generic auth-ish failure only if
        # the message clearly says so, else bubble the original exception.
        msg = str(exc)
        if _is_rate_limit_body(msg):
            raise _RateLimitError(msg) from exc
        if _is_date_param_error(msg) and "417" in msg:
            raise _DateParamError(msg) from exc
        if _is_subscription_body(msg) or "403" in msg or "entitle" in msg:
            raise WebullSubscriptionError(_SUBSCRIPTION_HINT) from exc
        if "401" in msg or "unauthorized" in msg.lower() or "auth" in msg.lower():
            raise WebullAuthError(_auth_hint(self.region)) from exc
        raise

    def _translate_status(self, status: int, detail: Any) -> None:
        if status == 403:
            raise WebullSubscriptionError(_SUBSCRIPTION_HINT)
        if status == 401:
            # The live US API returns 401 (not 403) for a missing OpenAPI
            # Quotes Subscription, with a body message containing
            # "subscribe" / "insufficient permission". Discriminate by body
            # so a genuine bad-token/bad-key 401 still maps to WebullAuthError.
            if _is_subscription_body(detail):
                raise WebullSubscriptionError(_SUBSCRIPTION_HINT)
            raise WebullAuthError(_auth_hint(self.region))
        if status == 417 and _is_date_param_error(detail):
            # The live API rejects start_date values it considers out of range
            # (HTTP 417, OAUTH_OPENAPI_PARAM_ERR "invalid start_date").  This
            # is raised as a sentinel so get_fills can catch it and stop the
            # backward date walk gracefully instead of propagating an error.
            raise _DateParamError(str(detail))
        if status == 429 or _is_rate_limit_body(detail):
            # Order-history endpoint is rate-limited (2 req/2 s). Raise a
            # sentinel so get_fills can back off and retry rather than aborting
            # the whole import.
            raise _RateLimitError(str(detail))
        # Other 4xx/5xx: surface a clear, non-secret message.
        from .errors import WebullError

        raise WebullError(f"Webull OpenAPI request failed (HTTP {status}).")

    def _resolve_account_id(self) -> str:
        """Fetch and cache the first Webull account id for this app.

        Used by get_positions / get_account where a single account context is
        expected. For fill import across all accounts use _resolve_account_ids().
        """
        if self._account_id:
            return self._account_id
        ids = self._resolve_account_ids()
        if not ids:
            from .errors import WebullError

            raise WebullError("No Webull account is associated with these credentials.")
        self._account_id = ids[0]
        return self._account_id

    def _resolve_account_ids(self) -> list[str]:
        """Return the ids of ALL Webull accounts linked to these credentials.

        The user may have multiple account types (MARGIN, CASH, …) each with
        their own independent order history. get_fills() must iterate all of
        them or history is silently incomplete.
        """
        _, trade = self._clients()
        payload = self._guard(trade.account_v2.get_account_list)
        accounts = payload if isinstance(payload, list) else mappers.extract_orders(payload)
        return [
            str(mappers._first(a, "accountId", "account_id", "id", default=""))
            for a in accounts
            if mappers._first(a, "accountId", "account_id", "id", default="")
        ]

    # ------------------------------------------------------------------ #
    # DataProvider protocol
    # ------------------------------------------------------------------ #
    def get_candles(self, symbol: str, day: date, timeframe: str = "1m") -> list[Candle]:
        """Recent 1-min bars for ``symbol`` filtered to the ET calendar ``day``.

        The window boundaries are passed to Webull as ``start_time``/``end_time``
        (see ``_fetch_candles``), so historical days are retrievable — not just
        the most-recent bars. Ranges wider than ``_MAX_BARS`` are still capped.
        """
        start = datetime.combine(day, dtime.min, tzinfo=UTC) - timedelta(days=1)
        end = datetime.combine(day, dtime.max, tzinfo=UTC) + timedelta(days=1)
        want = _as_utc(datetime.combine(day, dtime.min, tzinfo=UTC)).date()
        return [c for c in self._fetch_candles(symbol, timeframe, start, end) if c.ts.date() == want]

    def get_candles_range(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[Candle]:
        start_u, end_u = _as_utc(start), _as_utc(end)
        if end_u < start_u:
            return []
        return self._fetch_candles(symbol, timeframe, start_u, end_u)

    def get_option_bars(
        self, contract: OptionContract, day: date, timeframe: str = "1m"
    ) -> list[Candle] | None:
        # Permanently None: the Webull OpenAPI Data API exposes no historical
        # option bars (asset categories are US_STOCK/US_ETF only; real-time
        # option quotes need a separate OPRA sub). Verified against the docs
        # 2026-07-10 — see docs/DEMO-M4.md § API drift. The server-side
        # synthetic BS fallback is the premium data path for live mode.
        return None

    def get_fills(self, since: datetime | None = None) -> list[Fill]:
        """All filled executions from Webull order history, mapped to `Fill`.

        Design notes
        ============
        * **order_v3**: We call ``trade.order_v3.get_order_history`` (not v2).
          The working reference implementation (webull-openapi-mcp) confirmed v3
          is the correct namespace on the live US API. Pagination in v3 is via
          ``last_client_order_id`` (v3 has no ``last_order_id`` param).

        * **Bounded date windows**: The live API rejects ``start_date`` values
          older than ~730 days (HTTP 417, OAUTH_OPENAPI_PARAM_ERR). We walk
          backward in ``_DATE_WINDOW_DAYS``-day chunks, starting from today (or
          the ``since`` date) and going back at most ``_HISTORY_LOOKBACK_DAYS``
          days. This ensures start_date is always within the accepted range.

        * **All accounts**: The user may have multiple account types (MARGIN,
          CASH, …). We query every account and merge results, deduping on
          ``provider_fill_id``.

        * **Rate limiting**: The endpoint is capped at 2 req/2 s. Every call
          routes through ``_guard``, which acquires the process-wide
          ``_RATE_GATE`` first, so pagination is spaced out automatically.

        ``since`` (if given) sets the earliest window boundary *and* post-filters
        the result so fills before that datetime are never returned.
        """
        _, trade = self._clients()
        account_ids = self._resolve_account_ids()
        if not account_ids:
            from .errors import WebullError

            raise WebullError("No Webull account is associated with these credentials.")

        today = _utc_today()
        if since is not None:
            since_u = _as_utc(since)
            horizon_date = since_u.date()
        else:
            horizon_date = today - timedelta(days=_HISTORY_LOOKBACK_DAYS)

        # Build the list of (window_start, window_end) date pairs in descending
        # order (most-recent first, walking toward the past).
        windows = list(_date_windows(horizon_date, today, _DATE_WINDOW_DAYS))

        collected: list[dict] = []
        seen_ids: set[str] = set()

        for account_id in account_ids:
            # Reassigning ``windows`` would NOT stop the for-loop below (it holds
            # its own iterator over the original list), so use an explicit flag
            # to break out of the backward window walk on a date-param rejection.
            date_wall_hit = False
            for win_start, win_end in windows:
                start_str = win_start.isoformat()
                end_str = win_end.isoformat()
                last_client_order_id: str | None = None

                for _ in range(_MAX_ORDER_PAGES):  # hard cap against infinite loops
                    try:
                        payload = self._guard(
                            trade.order_v3.get_order_history,
                            account_id,
                            _ORDER_PAGE_SIZE,
                            start_str,
                            end_str,
                            last_client_order_id,
                        )
                    except _DateParamError:
                        # The API rejected start_date as out-of-range (HTTP 417
                        # OAUTH_OPENAPI_PARAM_ERR). This window and all earlier
                        # ones will also fail, so stop the backward walk for
                        # this account entirely (windows are ordered most-recent
                        # first, so everything after this is even older).
                        date_wall_hit = True
                        break

                    page = mappers.extract_orders(payload)
                    if not page:
                        break
                    new_this_page = 0
                    for o in page:
                        oid = mappers.order_cursor_id(o)
                        if oid is not None and oid in seen_ids:
                            continue
                        if oid is not None:
                            seen_ids.add(oid)
                        collected.append(o)
                        new_this_page += 1
                    # Advance the cursor to the last order's client_order_id.
                    next_cursor = mappers.order_cursor_id(page[-1])
                    if (
                        new_this_page == 0
                        or next_cursor is None
                        or next_cursor == last_client_order_id
                    ):
                        break  # no progress → stop pagination for this window
                    last_client_order_id = next_cursor

                if date_wall_hit:
                    break  # stop the backward window walk for this account

        orders = [o for o in collected if mappers.is_filled(o)]
        fills = [mappers._map_fill(o) for o in orders]
        fills.sort(key=lambda f: f.ts)
        if since is not None:
            since_u = _as_utc(since)
            fills = [f for f in fills if _as_utc(f.ts) >= since_u]
        return fills

    def get_positions(self) -> list[Position]:
        _, trade = self._clients()
        account_id = self._resolve_account_id()
        # New SDK: account_v2 namespace for positions.
        payload = self._guard(trade.account_v2.get_account_position, account_id)
        return [mappers._map_position(h) for h in mappers.extract_holdings(payload)]

    def get_account(self) -> Account:
        _, trade = self._clients()
        account_id = self._resolve_account_id()
        # New SDK: account_v2 namespace for balance.
        payload = self._guard(trade.account_v2.get_account_balance, account_id)
        return mappers._map_account(payload, positions=self.get_positions())

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _fetch_candles(
        self, symbol: str, timeframe: str, start_u: datetime, end_u: datetime
    ) -> list[Candle]:
        md, _ = self._clients()
        interval = _TIMEFRAME_TO_WEBULL.get(timeframe, "M1")
        # Convert the UTC window boundaries to millisecond epoch integers, which
        # is the format Webull's get_history_bar expects for start_time/end_time.
        start_ms = int(start_u.timestamp() * 1000)
        end_ms = int(end_u.timestamp() * 1000)
        # New SDK: data_client.market_data.get_history_bar supports start_time and
        # end_time (epoch ms) so Webull bounds the result to the requested window.
        # Passing only count returned the most-recent N bars regardless of date,
        # causing empty results for any day older than the ~1200-bar window.
        # NOTE: count is still capped at _MAX_BARS (1200). For multi-day ranges
        # that span more than ~1200 bars (e.g. several days of 1-min data), only
        # the most recent 1200 bars within [start_time, end_time] are returned.
        # Callers that need more data should paginate by walking end_time backward.
        # The single-day replay path (get_candles) stays well under this limit.
        payload = self._guard(
            md.market_data.get_history_bar,
            symbol, "US_STOCK", interval, str(_MAX_BARS),
            None, None,
            start_ms, end_ms,
        )
        bars = [mappers._map_bar(b, symbol, timeframe) for b in mappers.extract_bars(payload)]
        # Client-side filter as a final guard (Webull may return bars slightly
        # outside the requested window depending on bar boundary alignment).
        bars = [c for c in bars if start_u <= _as_utc(c.ts) <= end_u]
        bars.sort(key=lambda c: c.ts)
        return bars


# --------------------------------------------------------------------------- #
# Module-level helpers (kept pure for direct unit-testing)
# --------------------------------------------------------------------------- #

class _DateParamError(Exception):
    """Internal sentinel: HTTP 417 with an invalid start_date body.

    Raised by ``_translate_status`` and caught inside ``get_fills`` to stop
    the backward date-window walk without propagating to the caller.
    Never escapes the provider module.
    """


class _RateLimitError(Exception):
    """Internal sentinel: HTTP 429 TOO_MANY_REQUESTS.

    Raised by ``_translate_status`` and caught inside ``get_fills`` to trigger
    an exponential backoff + retry rather than aborting the whole import.
    Never escapes the provider module.
    """


# Keywords that identify a rate-limit rejection.
_RATE_LIMIT_SIGNALS = ("too_many_requests", "too many requests", "rate limit")


def _is_rate_limit_body(detail: Any) -> bool:
    """Return True when a response body looks like a rate-limit rejection."""
    if not detail:
        return False
    lowered = str(detail).lower()
    return any(signal in lowered for signal in _RATE_LIMIT_SIGNALS)


# Keywords that identify a 417 as a date-parameter rejection (not a generic 417).
_DATE_PARAM_SIGNALS = ("invalid start_date", "param_err", "parameter error", "start_date")


def _is_date_param_error(detail: Any) -> bool:
    """Return True when a 417 response body looks like an invalid-start_date rejection."""
    if not detail:
        return False
    lowered = str(detail).lower()
    return any(signal in lowered for signal in _DATE_PARAM_SIGNALS)


_SUBSCRIPTION_HINT = (
    "Market data requires quotes subscription. "
    "Subscribe at: https://www.webullapp.com/quote "
    "Guide: https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes"
)

# Subscription-signal keywords: case-insensitive.  Any of these in the response
# body means the 401 (or 403) is an entitlement failure, NOT a credential failure.
_SUBSCRIPTION_SIGNALS = ("subscribe", "insufficient permission", "quotes subscription")


def _is_subscription_body(text: Any) -> bool:
    """Return True if *text* contains a subscription / entitlement signal.

    Used to discriminate a missing-subscription 401 from a bad-token 401.
    """
    if not text:
        return False
    lowered = str(text).lower()
    return any(signal in lowered for signal in _SUBSCRIPTION_SIGNALS)


def _auth_hint(region: str) -> str:
    return (
        "Webull authentication failed (HTTP 401). Check WEBULL_APP_KEY / "
        "WEBULL_APP_SECRET in your .env and that the key is active for region "
        f"'{region}'."
    )


def _http_status(obj: Any) -> int | None:
    """Best-effort extraction of an HTTP status code from an exception or a
    response object, across the several shapes the SDK/HTTP layer may use."""
    if obj is None:
        return None
    for attr in ("status_code", "http_status", "status", "code"):
        val = getattr(obj, attr, None)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    # Some SDK exceptions expose a getter.
    getter = getattr(obj, "get_http_status", None)
    if callable(getter):
        try:
            v = getter()
            if isinstance(v, int):
                return v
        except Exception:  # noqa: BLE001
            return None
    return None


def _payload(resp: Any) -> Any:
    """Return the JSON body of a response object, or the object itself if it is
    already a plain dict/list (some SDK calls return parsed data directly)."""
    if isinstance(resp, (dict, list)) or resp is None:
        return resp
    json_getter = getattr(resp, "json", None)
    if callable(json_getter):
        return json_getter()
    return resp
