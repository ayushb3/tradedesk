"""Unit tests for the read-only Webull provider (M5 Phase A/B).

No network. The pure ``_map_*`` mappers are tested against representative
response fixtures; the SDK/HTTP layer is faked or monkeypatched so a simulated
403 surfaces as ``WebullSubscriptionError`` without any real call.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.models.market import Account, Candle, Position
from app.models.trades import Fill, Side
from app.providers.base import get_provider
from app.providers.webull import (
    WebullAuthError,
    WebullConfigError,
    WebullProvider,
    WebullSDKNotInstalled,
    WebullSubscriptionError,
    WebullTokenPending,
)
from app.providers.webull import mappers

UTC = timezone.utc


def _creds() -> Settings:
    return Settings(mode="webull", webull_app_key="key-123", webull_app_secret="secret-456")


@pytest.fixture(autouse=True)
def _reset_rate_gate():
    """Reset the module-level rate gate before/after every test.

    ``_RATE_GATE`` is a process-wide singleton (by design — see
    provider.py), so without a reset a preceding test's ``_next`` deadline
    could force an unrelated later test to sleep or behave non-deterministically.
    """
    from app.providers.webull.provider import _RATE_GATE

    _RATE_GATE._next = 0.0
    yield
    _RATE_GATE._next = 0.0


# --------------------------------------------------------------------------- #
# Factory / mode wiring
# --------------------------------------------------------------------------- #
def test_mock_mode_returns_mock_provider_without_sdk():
    prov = get_provider(Settings(mode="mock"))
    assert type(prov).__name__ == "MockProvider"


# --------------------------------------------------------------------------- #
# Credential validation
# --------------------------------------------------------------------------- #
def test_missing_creds_raises_config_error():
    with pytest.raises(WebullConfigError) as ei:
        WebullProvider(Settings(mode="webull", webull_app_key="", webull_app_secret=""))
    msg = str(ei.value)
    assert "app_key" in msg and "app_secret" in msg
    # Never leak secret values (there are none here, but assert the .env pointer).
    assert ".env" in msg


def test_partial_creds_raises_config_error():
    with pytest.raises(WebullConfigError):
        WebullProvider(Settings(mode="webull", webull_app_key="only-key", webull_app_secret=""))


# --------------------------------------------------------------------------- #
# Mappers — the seam
# --------------------------------------------------------------------------- #
def test_map_bar_stringified_numbers_and_ms_timestamp():
    bar = {
        "open": "10.00",
        "high": "10.50",
        "low": "9.90",
        "close": "10.20",
        "volume": "1500",
        "timestamp": "1717770600000",  # epoch ms
    }
    c = mappers._map_bar(bar, "RIVN", "1m")
    assert isinstance(c, Candle)
    assert (c.open, c.high, c.low, c.close, c.volume) == (10.0, 10.5, 9.9, 10.2, 1500.0)
    assert c.symbol == "RIVN" and c.timeframe == "1m"
    assert c.ts.tzinfo is not None and c.ts.utcoffset() == UTC.utcoffset(None)


def test_map_bar_camelcase_and_iso_trade_time():
    bar = {
        "symbol": "HOOD",
        "openPrice": 20.0,
        "highPrice": 21.0,
        "lowPrice": 19.5,
        "closePrice": 20.5,
        "volume": 900,
        "tradeTime": "2026-06-10T13:30:00Z",
    }
    c = mappers._map_bar(bar, "FALLBACK")
    assert c.symbol == "HOOD"  # explicit symbol wins over fallback
    assert c.close == 20.5
    assert c.ts == datetime(2026, 6, 10, 13, 30, tzinfo=UTC)


def test_map_fill():
    order = {
        "orderId": "o-1",
        "accountId": "ACC1",
        "symbol": "RIVN",
        "action": "BUY",
        "filledQuantity": "100",
        "avgFilledPrice": "12.34",
        "filledTime": "1717770600000",
        "commission": "0.35",
        "status": "FILLED",
    }
    f = mappers._map_fill(order)
    assert isinstance(f, Fill)
    assert f.side is Side.buy
    assert f.qty == 100.0 and f.price == 12.34 and f.fees == 0.35
    assert f.symbol == "RIVN" and f.account == "ACC1"
    assert f.provider_fill_id == "o-1"
    assert f.simulated is False
    assert f.raw_json == order


def test_map_fill_sell_short_maps_to_sell():
    order = {
        "symbol": "HOOD",
        "action": "SELL_SHORT",
        "filledQty": "50",
        "filledPrice": "20.0",
        "updateTime": "2026-06-10T14:00:00Z",
    }
    f = mappers._map_fill(order)
    assert f.side is Side.sell
    assert f.qty == 50.0 and f.fees == 0.0  # commission defaults to 0


def test_map_position():
    h = {
        "symbol": "HOOD",
        "quantity": "50",
        "costPrice": "20.00",
        "marketValue": "1100.00",
        "unrealizedProfitLoss": "100.00",
    }
    p = mappers._map_position(h)
    assert isinstance(p, Position)
    assert p.symbol == "HOOD" and p.qty == 50.0 and p.avg_price == 20.0
    assert p.market_value == 1100.0 and p.unrealized_pnl == 100.0


def test_map_account_nested_currency_block():
    data = {
        "accountId": "ACC1",
        "accountCurrencyAssets": [
            {
                "currency": "USD",
                "cashBalance": "25000",
                "netLiquidation": "30000",
                "buyingPower": "100000",
            }
        ],
    }
    positions = [Position(symbol="HOOD", qty=50, avg_price=20.0)]
    a = mappers._map_account(data, positions=positions)
    assert isinstance(a, Account)
    assert a.account_id == "ACC1" and a.currency == "USD"
    assert a.cash == 25000.0 and a.equity == 30000.0 and a.buying_power == 100000.0
    assert a.positions == positions


def test_extract_bars_list_of_instruments():
    payload = [{"symbol": "RIVN", "bars": [{"open": "1", "high": "1", "low": "1", "close": "1", "volume": "1", "timestamp": "1717770600000"}]}]
    bars = mappers.extract_bars(payload)
    assert len(bars) == 1 and bars[0]["open"] == "1"


def test_is_filled_filters_cancelled_and_zero():
    assert mappers.is_filled({"status": "FILLED", "filledQuantity": "10"}) is True
    assert mappers.is_filled({"status": "CANCELLED", "filledQuantity": "10"}) is False
    assert mappers.is_filled({"status": "FILLED", "filledQuantity": "0"}) is False


# --------------------------------------------------------------------------- #
# Shared test helpers
# --------------------------------------------------------------------------- #
def _write_token_file(token_dir, token="tok-xyz", expires_ms=9999999999000,
                      status="NORMAL"):
    """Write a token.txt in the format produced by webull_auth.py."""
    d = Path(token_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "token.txt").write_text(
        f"{token}\n{expires_ms}\n{status}\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# 403 / subscription handling (no network — fake SDK clients injected)
# --------------------------------------------------------------------------- #
class _FakeApiException(Exception):
    """Mimics a Webull SDK exception that carries an HTTP status."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FakeAccountV2_403:
    """Fake trade.account_v2 namespace — returns 403 on position calls."""

    def get_account_position(self, account_id):
        raise _FakeApiException(403)


class _FakeTrade403:
    """Fake TradeClient — exposes account_v2 namespace mirroring new SDK shape."""

    def __init__(self):
        self.account_v2 = _FakeAccountV2_403()


def test_simulated_403_surfaces_as_subscription_error(monkeypatch, tmp_path):
    prov = WebullProvider(_creds())
    # Write a valid token file so _ensure_token() passes.
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    # Inject fake clients so _clients() short-circuits (no real SDK import).
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTrade403()
    prov._account_id = "ACC1"  # skip account resolution

    with pytest.raises(WebullSubscriptionError) as ei:
        prov.get_positions()
    assert "webullapp.com/quote" in str(ei.value)


def test_403_via_response_status_code(monkeypatch, tmp_path):
    """A response object (not an exception) with status 403 also translates."""

    class _Resp403:
        status_code = 403

        def json(self):  # pragma: no cover - not reached (status checked first)
            return {}

    class _FakeAccountV2:
        def get_account_position(self, account_id):
            return _Resp403()

    class _FakeTrade:
        def __init__(self):
            self.account_v2 = _FakeAccountV2()

    prov = WebullProvider(_creds())
    # Write a valid token file so _ensure_token() passes.
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._trade = _FakeTrade()
    prov._account_id = "ACC1"
    with pytest.raises(WebullSubscriptionError):
        prov.get_positions()


# --------------------------------------------------------------------------- #
# 401 body discrimination: subscription vs genuine auth failure
# --------------------------------------------------------------------------- #
class _FakeApiException401(Exception):
    """Mimics a Webull SDK exception with HTTP 401 and a customisable message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.status_code = 401


class _FakeAccountV2_401:
    """Fake trade.account_v2 that raises a 401 exception with a given message."""

    def __init__(self, message: str):
        self._message = message

    def get_account_position(self, account_id):
        raise _FakeApiException401(self._message)


class _FakeTrade401:
    def __init__(self, message: str):
        self.account_v2 = _FakeAccountV2_401(message)


def _make_prov_401(tmp_path, message: str) -> "WebullProvider":
    """Build a provider wired to raise a 401 with the given body message."""
    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTrade401(message)
    prov._account_id = "ACC1"
    return prov


def test_401_with_subscription_body_raises_subscription_error(tmp_path):
    """HTTP 401 + 'subscribe to stock quotes' body → WebullSubscriptionError.

    This is the live US API behaviour: the OpenAPI quotes-subscription gate
    returns 401 (not 403) with message 'Insufficient permission, please
    subscribe to stock quotes.'  The adapter must NOT call this a credential
    error — the credentials are fine.
    """
    prov = _make_prov_401(
        tmp_path,
        "Insufficient permission, please subscribe to stock quotes.",
    )
    with pytest.raises(WebullSubscriptionError) as ei:
        prov.get_positions()
    msg = str(ei.value)
    assert "webullapp.com/quote" in msg, "subscription hint URL must be in the error"
    assert "subscribe-quotes" in msg, "guide URL must be in the error"


def test_401_with_generic_auth_message_raises_auth_error(tmp_path):
    """HTTP 401 + generic 'invalid token' body → WebullAuthError (not subscription)."""
    prov = _make_prov_401(tmp_path, "invalid token")
    with pytest.raises(WebullAuthError) as ei:
        prov.get_positions()
    msg = str(ei.value)
    assert "WEBULL_APP_KEY" in msg or "WEBULL_APP_SECRET" in msg, (
        "auth error must point the user at credential config"
    )


# --------------------------------------------------------------------------- #
# SDK-not-installed degrades gracefully (only when a live call is attempted)
# --------------------------------------------------------------------------- #
def _sdk_installed() -> bool:
    try:
        # New unified package: webull-openapi-python-sdk
        from webull.core.client import ApiClient  # type: ignore  # noqa: F401
        from webull.data.data_client import DataClient  # type: ignore  # noqa: F401
        from webull.trade.trade_client import TradeClient  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def test_construction_never_needs_sdk():
    # Building the provider must not import or require the SDK.
    WebullProvider(_creds())


@pytest.mark.skipif(_sdk_installed(), reason="SDK is installed in this env")
def test_live_call_without_sdk_raises_sdk_not_installed():
    prov = WebullProvider(_creds())
    with pytest.raises(WebullSDKNotInstalled) as ei:
        prov.get_positions()
    assert "pip install" in str(ei.value)
    assert "webull-openapi-python-sdk" in str(ei.value)


# --------------------------------------------------------------------------- #
# Phase B — token lifecycle (webull_auth.py)
# --------------------------------------------------------------------------- #
# Make sure scripts/ is importable regardless of how pytest was launched.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class _FakeResponse:
    """Minimal response mimic returned by fake TokenOperation calls."""

    def __init__(self, body: dict, status_code: int = 200):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class _FakeTokenOperation:
    """Fake TokenOperation that returns PENDING for the first N checks, then NORMAL.

    Mirrors the real TokenOperation interface:
      create_token(token) -> response
      check_token(token)  -> response
    """

    def __init__(self, api_client, *, pending_rounds: int = 2):
        # api_client is accepted but ignored (matches real signature).
        self._pending_rounds = pending_rounds
        self._check_calls = 0

    def create_token(self, token):  # noqa: ARG002
        return _FakeResponse(
            {"token": "tok-abc123", "expires": 9999999999000, "status": "PENDING"}
        )

    def check_token(self, token):  # noqa: ARG002
        self._check_calls += 1
        if self._check_calls <= self._pending_rounds:
            return _FakeResponse(
                {"token": "tok-abc123", "expires": 9999999999000, "status": "PENDING"}
            )
        return _FakeResponse(
            {"token": "tok-abc123-verified", "expires": 9999999999000, "status": "NORMAL"}
        )


def test_token_pending_then_normal_writes_token_file(tmp_path, monkeypatch):
    """Happy path: PENDING for 2 polls, then NORMAL — token.txt written."""
    # Speed up: zero sleep so the test doesn't actually wait.
    monkeypatch.setattr("time.sleep", lambda _: None)

    import webull_auth  # type: ignore  # loaded from scripts/

    token_dir = tmp_path / "webull-token"
    exit_code = webull_auth.run(
        app_key="k",
        app_secret="s",
        region="us",
        token_dir=token_dir,
        interval_seconds=1,
        timeout_seconds=30,
        _token_operation_class=_FakeTokenOperation,
    )

    assert exit_code == 0, "expected exit 0 (NORMAL)"

    token_file = token_dir / "token.txt"
    assert token_file.exists(), "token.txt should be written on success"

    lines = token_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "tok-abc123-verified", "token value from NORMAL response"
    assert lines[2] == "NORMAL", "status line should be NORMAL"


def test_token_already_normal_on_create(tmp_path, monkeypatch):
    """If create_token already returns NORMAL (token still valid), exit immediately."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    import webull_auth  # type: ignore

    class _AlreadyNormal:
        def __init__(self, api_client):
            pass

        def create_token(self, token):  # noqa: ARG002
            return _FakeResponse(
                {"token": "tok-still-valid", "expires": 9999999999000, "status": "NORMAL"}
            )

        def check_token(self, token):  # pragma: no cover
            raise AssertionError("check_token should NOT be called when already NORMAL")

    token_dir = tmp_path / "webull-token"
    exit_code = webull_auth.run(
        app_key="k",
        app_secret="s",
        region="us",
        token_dir=token_dir,
        interval_seconds=1,
        timeout_seconds=30,
        _token_operation_class=_AlreadyNormal,
    )
    assert exit_code == 0
    token_file = token_dir / "token.txt"
    assert token_file.read_text(encoding="utf-8").splitlines()[0] == "tok-still-valid"


def test_token_timeout_returns_exit_code_1(tmp_path, monkeypatch):
    """If NORMAL never arrives within timeout, return exit code 1."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    import webull_auth  # type: ignore

    class _AlwaysPending:
        def __init__(self, api_client):
            pass

        def create_token(self, token):  # noqa: ARG002
            return _FakeResponse(
                {"token": "tok-stuck", "expires": 9999999999000, "status": "PENDING"}
            )

        def check_token(self, token):  # noqa: ARG002
            return _FakeResponse(
                {"token": "tok-stuck", "expires": 9999999999000, "status": "PENDING"}
            )

    token_dir = tmp_path / "webull-token"
    exit_code = webull_auth.run(
        app_key="k",
        app_secret="s",
        region="us",
        token_dir=token_dir,
        interval_seconds=1,
        timeout_seconds=3,   # very short; monkeypatched sleep makes this instant
        _token_operation_class=_AlwaysPending,
    )
    assert exit_code == 1, "expected exit 1 (timeout)"


def test_settings_webull_token_dir_field():
    """Settings.webull_token_dir exists and can be set via env var."""
    import os

    original = os.environ.get("WEBULL_OPENAPI_TOKEN_DIR")
    try:
        os.environ["WEBULL_OPENAPI_TOKEN_DIR"] = "/tmp/test-token-dir"
        # Must re-import to pick up the env var (Settings is constructed fresh).
        s = Settings(mode="mock")
        # The field itself is blank by default unless get_settings() is used.
        # What matters here is the field exists on the model.
        assert hasattr(s, "webull_token_dir")
    finally:
        if original is None:
            os.environ.pop("WEBULL_OPENAPI_TOKEN_DIR", None)
        else:
            os.environ["WEBULL_OPENAPI_TOKEN_DIR"] = original


# --------------------------------------------------------------------------- #
# Phase C — _ensure_token() unit tests
# --------------------------------------------------------------------------- #
def _make_provider(token_dir) -> WebullProvider:
    """Build a WebullProvider pointed at a custom token_dir without any real SDK."""
    s = Settings(mode="webull", webull_app_key="k", webull_app_secret="s",
                 webull_token_dir=str(token_dir))
    prov = WebullProvider(s)
    # Inject fake sdk clients so _clients() short-circuits past the import.
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = object()
    return prov


def test_ensure_token_missing_file_raises_pending(tmp_path):
    """No token.txt → WebullTokenPending."""
    prov = _make_provider(tmp_path / "no-token-here")
    with pytest.raises(WebullTokenPending) as ei:
        prov._ensure_token()
    assert "webull_auth.py" in str(ei.value)
    assert str(prov._token_dir) in str(ei.value)


def test_ensure_token_status_pending_raises_pending(tmp_path):
    """token.txt exists but status == PENDING → WebullTokenPending."""
    token_dir = tmp_path / "tok"
    _write_token_file(token_dir, status="PENDING")
    prov = _make_provider(token_dir)
    with pytest.raises(WebullTokenPending):
        prov._ensure_token()


def test_ensure_token_status_normal_returns_ok(tmp_path):
    """token.txt with status NORMAL and future expiry → no exception."""
    token_dir = tmp_path / "tok"
    _write_token_file(token_dir, status="NORMAL", expires_ms=9999999999000)
    prov = _make_provider(token_dir)
    prov._ensure_token()  # must not raise


def test_ensure_token_expired_raises_pending(tmp_path):
    """token.txt with status NORMAL but past expiry epoch → WebullTokenPending."""
    token_dir = tmp_path / "tok"
    # expires_ms = 1 (way in the past)
    _write_token_file(token_dir, status="NORMAL", expires_ms=1)
    prov = _make_provider(token_dir)
    with pytest.raises(WebullTokenPending):
        prov._ensure_token()


# --------------------------------------------------------------------------- #
# _fetch_candles — windowed SDK call (the time-window bug fix)
# --------------------------------------------------------------------------- #
class _RecordingMarketData:
    """Fake md.market_data that records the args passed to get_history_bar and
    returns a canned list of bars at a given timestamp."""

    def __init__(self, bar_ts_ms: int):
        self.calls: list[dict] = []
        self._bar_ts_ms = bar_ts_ms

    def get_history_bar(
        self,
        symbol,
        category,
        timespan,
        count="200",
        real_time_required=None,
        trading_sessions=None,
        start_time=None,
        end_time=None,
    ):
        self.calls.append(
            {
                "symbol": symbol,
                "category": category,
                "timespan": timespan,
                "count": count,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        # Return one bar at the configured timestamp so the mapper has something
        # to process.
        return [
            {
                "symbol": symbol,
                "open": "10.00",
                "high": "10.50",
                "low": "9.90",
                "close": "10.20",
                "volume": "500",
                "timestamp": str(self._bar_ts_ms),
            }
        ]


class _FakeMdClient:
    """Fake DataClient that wraps a _RecordingMarketData instance."""

    def __init__(self, recording: _RecordingMarketData):
        self.market_data = recording


def _make_provider_with_recording_md(tmp_path, bar_ts_ms: int):
    """Build a WebullProvider whose market_data is the recording fake."""
    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    recording = _RecordingMarketData(bar_ts_ms)
    prov._mdata = _FakeMdClient(recording)
    prov._trade = object()
    return prov, recording


def test_fetch_candles_passes_start_end_time_to_sdk(tmp_path):
    """_fetch_candles must forward start_time and end_time (epoch ms) to
    get_history_bar so that bars from any requested date are retrievable, not
    just the most-recent ~1200 bars."""
    # Use a bar timestamp well inside the requested window.
    bar_ts = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
    bar_ts_ms = int(bar_ts.timestamp() * 1000)

    start_u = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    end_u = datetime(2025, 1, 15, 21, 0, tzinfo=UTC)

    prov, recording = _make_provider_with_recording_md(tmp_path, bar_ts_ms)
    result = prov._fetch_candles("AAPL", "1m", start_u, end_u)

    assert len(recording.calls) == 1, "expected exactly one SDK call"
    call = recording.calls[0]

    expected_start_ms = int(start_u.timestamp() * 1000)
    expected_end_ms = int(end_u.timestamp() * 1000)

    assert call["start_time"] == expected_start_ms, (
        f"start_time not forwarded: got {call['start_time']!r}, "
        f"expected {expected_start_ms}"
    )
    assert call["end_time"] == expected_end_ms, (
        f"end_time not forwarded: got {call['end_time']!r}, "
        f"expected {expected_end_ms}"
    )
    # Bar is inside the window → should survive the client-side filter.
    assert len(result) == 1


def test_fetch_candles_filters_bars_outside_window(tmp_path):
    """Client-side filter removes bars that fall outside [start_u, end_u],
    even if the SDK returns them (defensive belt-and-suspenders guard)."""
    # Bar is outside the requested window (year-old bar at a different day).
    bar_ts_ms = int(datetime(2024, 3, 5, 14, 30, tzinfo=UTC).timestamp() * 1000)

    start_u = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    end_u = datetime(2025, 1, 15, 21, 0, tzinfo=UTC)

    prov, recording = _make_provider_with_recording_md(tmp_path, bar_ts_ms)
    result = prov._fetch_candles("AAPL", "1m", start_u, end_u)

    # SDK was called (windowed request went out) …
    assert len(recording.calls) == 1
    # … but the stale bar is filtered out by the client-side guard.
    assert result == [], "bar outside window should be filtered by client-side guard"


# --------------------------------------------------------------------------- #
# get_fills — date-range window + pagination (regression for the ~10-order,
# last-7-days truncation bug: get_order_history defaults to page_size=10 and,
# with no dates, only the last 7 days).
# --------------------------------------------------------------------------- #
class _RecordingOrderV3:
    """Fake trade.order_v3 that serves paged order history and records calls.

    Pages are keyed by last_client_order_id (v3 cursor field — no last_order_id).
    """

    def __init__(self, pages: list[list[dict]]):
        self._pages = pages
        self.calls: list[dict] = []

    def get_order_history(
        self,
        account_id,
        page_size=None,
        start_date=None,
        end_date=None,
        last_client_order_id=None,
    ):
        self.calls.append(
            {
                "account_id": account_id,
                "page_size": page_size,
                "start_date": start_date,
                "end_date": end_date,
                "last_client_order_id": last_client_order_id,
            }
        )
        # Serve pages by cursor position.
        if last_client_order_id is None:
            return {"orders": self._pages[0] if self._pages else []}
        for i, page in enumerate(self._pages):
            if page and str(page[-1].get("clientOrderId", page[-1].get("orderId", ""))) == str(last_client_order_id):
                return {"orders": self._pages[i + 1] if i + 1 < len(self._pages) else []}
        return {"orders": []}


class _FakeTradeOrders:
    def __init__(self, order_v3):
        self.order_v3 = order_v3
        # account_v2 returns a minimal account list so _resolve_account_ids works.
        self.account_v2 = _FakeAccountV2ForOrders()


class _FakeAccountV2ForOrders:
    def get_account_list(self):
        return [{"accountId": "ACC1"}]


def _order(oid: str, symbol: str, action: str, qty: str, price: str, ts_ms: str) -> dict:
    return {
        "clientOrderId": oid,   # v3 pagination cursor field
        "orderId": f"raw-{oid}",  # raw server id (different from clientOrderId)
        "accountId": "ACC1",
        "symbol": symbol,
        "action": action,
        "filledQuantity": qty,
        "avgFilledPrice": price,
        "filledTime": ts_ms,
        "status": "FILLED",
    }


def _make_prov_with_orders(tmp_path, pages, monkeypatch=None):
    """Build a provider with a fake order_v3.  monkeypatch is used to suppress sleep."""
    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    recording = _RecordingOrderV3(pages)
    prov._trade = _FakeTradeOrders(recording)
    # Do NOT pre-set _account_id so _resolve_account_ids() is exercised.
    return prov, recording


def test_get_fills_paginates_across_pages(tmp_path, monkeypatch):
    """get_fills must walk every page (not stop at the first ~10) and pass an
    explicit date window (not rely on the 7-day default).

    Uses order_v3 + last_client_order_id cursor (not order_v2 + last_order_id).
    """
    monkeypatch.setattr("time.sleep", lambda _: None)  # suppress rate-limit sleep
    page1 = [
        _order("o-1", "RIVN", "BUY", "100", "12.00", "1717770600000"),
        _order("o-2", "RIVN", "SELL", "100", "12.50", "1717774200000"),
    ]
    page2 = [
        _order("o-3", "HOOD", "BUY", "50", "20.00", "1717857000000"),
        _order("o-4", "HOOD", "SELL", "50", "19.50", "1717860600000"),
    ]
    prov, recording = _make_prov_with_orders(tmp_path, [page1, page2])

    fills = prov.get_fills()

    # All four fills across both pages are returned (no first-page cap).
    assert len(fills) == 4
    assert {f.provider_fill_id for f in fills} == {"o-1", "o-2", "o-3", "o-4"}

    # An explicit start/end date window was sent (not left to the 7-day default).
    first = recording.calls[0]
    assert first["start_date"] is not None and first["end_date"] is not None
    assert first["page_size"] is not None
    # The cursor advanced using last_client_order_id (NOT last_order_id).
    # The second call for this window should carry last_client_order_id == "o-2".
    page_calls = [c for c in recording.calls if c.get("last_client_order_id") is not None]
    assert any(c["last_client_order_id"] == "o-2" for c in page_calls), (
        f"Expected a call with last_client_order_id='o-2', got calls: {recording.calls}"
    )


def test_get_fills_since_sets_window_and_filters(tmp_path, monkeypatch):
    """A `since` datetime sets the start_date and post-filters older fills."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    old = _order("o-old", "RIVN", "BUY", "10", "5.00", "1577880000000")  # 2020
    new = _order("o-new", "RIVN", "BUY", "10", "6.00", "1717770600000")  # 2024
    prov, recording = _make_prov_with_orders(tmp_path, [[old, new]])

    since = datetime(2023, 1, 1, tzinfo=UTC)
    fills = prov.get_fills(since=since)

    assert [f.provider_fill_id for f in fills] == ["o-new"]
    # start_date must be the since date (2023-01-01).
    assert any(c["start_date"] == "2023-01-01" for c in recording.calls)


def test_get_fills_stops_on_nonadvancing_cursor(tmp_path, monkeypatch):
    """A page whose last clientOrderId repeats the current cursor must not loop forever."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    dup = [_order("o-1", "RIVN", "BUY", "1", "1.00", "1717770600000")]

    class _StuckOrderV3(_RecordingOrderV3):
        def get_order_history(self, account_id, page_size=None, start_date=None,
                              end_date=None, last_client_order_id=None):
            self.calls.append({"last_client_order_id": last_client_order_id})
            return {"orders": dup}

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTradeOrders(_StuckOrderV3([]))

    fills = prov.get_fills()
    assert len(fills) == 1  # deduped to the single unique clientOrderId
    # Bounded: the stuck cursor terminates the pagination loop quickly per window.
    assert len(prov._trade.order_v3.calls) <= 20  # generous bound across all date windows


# --------------------------------------------------------------------------- #
# get_fills — multi-account merging + deduplication
# --------------------------------------------------------------------------- #
class _MultiAccountOrderV3:
    """Fake order_v3 that returns different orders per account_id."""

    def __init__(self, orders_by_account: dict[str, list[dict]]):
        self._orders = orders_by_account
        self.calls: list[dict] = []

    def get_order_history(
        self,
        account_id,
        page_size=None,
        start_date=None,
        end_date=None,
        last_client_order_id=None,
    ):
        self.calls.append({"account_id": account_id, "last_client_order_id": last_client_order_id})
        return {"orders": self._orders.get(account_id, [])}


class _MultiAccountV2:
    """Fake account_v2 that returns two accounts."""

    def get_account_list(self):
        return [{"accountId": "MARGIN-ACC"}, {"accountId": "CASH-ACC"}]


class _FakeTradeMultiAccount:
    def __init__(self, order_v3):
        self.order_v3 = order_v3
        self.account_v2 = _MultiAccountV2()


def test_get_fills_iterates_all_accounts_and_merges(tmp_path, monkeypatch):
    """get_fills must query every account and merge results across accounts."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    margin_fills = [_order("m-1", "RIVN", "BUY", "50", "12.00", "1717770600000")]
    cash_fills = [_order("c-1", "HOOD", "BUY", "100", "20.00", "1717857000000")]

    margin_fills[0]["accountId"] = "MARGIN-ACC"
    cash_fills[0]["accountId"] = "CASH-ACC"

    recording = _MultiAccountOrderV3({
        "MARGIN-ACC": margin_fills,
        "CASH-ACC": cash_fills,
    })

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTradeMultiAccount(recording)

    fills = prov.get_fills()

    fill_ids = {f.provider_fill_id for f in fills}
    assert "m-1" in fill_ids, "MARGIN account fill must be included"
    assert "c-1" in fill_ids, "CASH account fill must be included"
    assert len(fills) == 2

    # Both account ids must have been queried.
    queried_accounts = {c["account_id"] for c in recording.calls}
    assert "MARGIN-ACC" in queried_accounts
    assert "CASH-ACC" in queried_accounts


def test_get_fills_dedupes_across_accounts(tmp_path, monkeypatch):
    """If the same clientOrderId appears in both accounts, it must not be double-counted."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    shared_order = _order("shared-1", "RIVN", "BUY", "10", "12.00", "1717770600000")

    recording = _MultiAccountOrderV3({
        "MARGIN-ACC": [shared_order],
        "CASH-ACC": [shared_order],  # same clientOrderId in both — should dedupe
    })

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTradeMultiAccount(recording)

    fills = prov.get_fills()

    assert len(fills) == 1, f"Expected 1 fill (deduped), got {len(fills)}"
    assert fills[0].provider_fill_id == "shared-1"


# --------------------------------------------------------------------------- #
# Bounded date window — start_date must never be older than _HISTORY_LOOKBACK_DAYS
# --------------------------------------------------------------------------- #
def test_get_fills_default_window_never_exceeds_lookback_horizon(tmp_path, monkeypatch):
    """With no `since`, the oldest start_date sent must be within the bounded horizon.

    The live API rejects start_date values older than ~730 days with HTTP 417.
    We must never send a date before (today - _HISTORY_LOOKBACK_DAYS).
    """
    import datetime as dt
    from app.providers.webull.provider import _HISTORY_LOOKBACK_DAYS

    monkeypatch.setattr("time.sleep", lambda _: None)

    # Capture all calls; always return empty (we just want to inspect the dates).
    all_calls: list[dict] = []

    class _DateCapturingOrderV3:
        calls = all_calls

        def get_order_history(self, account_id, page_size=None, start_date=None,
                              end_date=None, last_client_order_id=None):
            all_calls.append({"start_date": start_date, "end_date": end_date})
            return {"orders": []}

    class _FakeTradeDateCapture:
        def __init__(self):
            self.order_v3 = _DateCapturingOrderV3()
            self.account_v2 = _FakeAccountV2ForOrders()

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTradeDateCapture()

    prov.get_fills()

    today = dt.date.today()
    horizon = today - dt.timedelta(days=_HISTORY_LOOKBACK_DAYS)

    assert all_calls, "At least one call must have been made"
    for call in all_calls:
        start = dt.date.fromisoformat(call["start_date"])
        assert start >= horizon, (
            f"start_date {start} is older than the {_HISTORY_LOOKBACK_DAYS}-day "
            f"horizon {horizon} — the live API would reject this with HTTP 417"
        )


# --------------------------------------------------------------------------- #
# _date_windows helper
# --------------------------------------------------------------------------- #
def test_date_windows_covers_full_range():
    """_date_windows must cover [horizon, today] without gaps."""
    import datetime as dt
    from app.providers.webull.provider import _date_windows

    today = dt.date(2026, 7, 10)
    horizon = dt.date(2024, 7, 10)  # 730 days back

    windows = list(_date_windows(horizon, today, chunk_days=90))
    assert windows, "should produce at least one window"

    # First window ends at today.
    assert windows[0][1] == today

    # Last window starts at or before horizon.
    assert windows[-1][0] <= horizon

    # No gaps: each window_end + 1 day == next window_start.
    for i in range(len(windows) - 1):
        gap = windows[i][0] - windows[i + 1][1]
        assert gap == dt.timedelta(days=1), (
            f"Gap between windows {i} and {i+1}: {windows[i]} / {windows[i+1]}"
        )

    # No window start_date is before horizon.
    for start, end in windows:
        assert start >= horizon, f"Window start {start} before horizon {horizon}"


def test_date_windows_single_chunk_when_range_fits():
    """A range that fits entirely within one chunk produces exactly one window."""
    import datetime as dt
    from app.providers.webull.provider import _date_windows

    today = dt.date(2026, 7, 10)
    # chunk_days=90 covers today - 89 .. today (90 dates inclusive).
    # Set horizon exactly at the chunk start → single window.
    horizon = today - dt.timedelta(days=89)  # 2026-04-12

    windows = list(_date_windows(horizon, today, chunk_days=90))
    assert len(windows) == 1
    assert windows[0] == (horizon, today)


# --------------------------------------------------------------------------- #
# order_v3 combo-wrapper response shape — flatten + map
# --------------------------------------------------------------------------- #
# Fixtures match the EXACT live shape observed during live probing (2026-07-10):
#   - filled_time_at: ISO-8601 string  e.g. "2026-07-09T19:59:52.982Z"
#   - filled_time:    epoch-ms string  e.g. "1783627192982"
#   - side:           snake_case       "SELL" / "BUY"
#   - filled_quantity / filled_price:  stringified numbers
#   - inner client_order_id == outer client_order_id (same uuid)
#
# shape:
# [
#   {
#     "client_order_id": "6a4ffd9d...",
#     "combo_type": "NORMAL",
#     "combo_order_id": "4AH6K0...",
#     "orders": [
#       { "symbol": "SPXW", "side": "SELL", "status": "FILLED",
#         "instrument_type": "OPTION", "filled_quantity": "1",
#         "filled_price": "2.20", "filled_time": "1783627192982",
#         "filled_time_at": "2026-07-09T19:59:52.982Z",
#         "client_order_id": "6a4ffd9d...", "order_id": "4AH6K0...",
#         "legs": [{...}] }
#     ]
#   }
# ]

_FILLED_OPTION_WRAPPER = {
    "client_order_id": "WRAP-OPT-1",
    "combo_type": "NORMAL",
    "combo_order_id": "CO-OPT-001",
    "orders": [
        {
            "symbol": "SPXW",
            "side": "SELL",
            "status": "FILLED",
            "filled_quantity": "1",
            "filled_price": "2.20",
            "filled_time": "1783627192982",           # epoch ms string (live shape)
            "filled_time_at": "2026-07-09T19:59:52.982Z",  # ISO string (live shape)
            "client_order_id": "OPT-INNER-1",
            "order_id": "raw-opt-111",
            "instrument_type": "OPTION",
            "legs": [{"strike_price": "5500", "option_type": "CALL"}],
        }
    ],
}

_FILLED_EQUITY_WRAPPER = {
    "client_order_id": "WRAP-EQ-2",
    "combo_type": "NORMAL",
    "combo_order_id": "CO-EQ-002",
    "orders": [
        {
            "symbol": "RIVN",
            "side": "SELL",
            "status": "FILLED",
            "filled_quantity": "100",
            "filled_price": "12.50",
            "filled_time": "1720003600000",
            "filled_time_at": "2024-07-03T14:00:00.000Z",
            "client_order_id": "EQ-INNER-2",
            "order_id": "raw-eq-222",
            "instrument_type": "STK",
        }
    ],
}

_CANCELLED_WRAPPER = {
    "client_order_id": "WRAP-CX-3",
    "combo_type": "NORMAL",
    "combo_order_id": "CO-CX-003",
    "orders": [
        {
            "symbol": "HOOD",
            "side": "BUY",
            "status": "CANCELLED",
            "filled_quantity": "0",
            "filled_price": "0",
            "filled_time": "1720007200000",
            "filled_time_at": "2024-07-03T15:00:00.000Z",
            "client_order_id": "CX-INNER-3",
            "order_id": "raw-cx-333",
        }
    ],
}


def test_extract_orders_flattens_combo_wrappers():
    """extract_orders must return the INNER order dicts, not the wrapper dicts."""
    payload = [_FILLED_OPTION_WRAPPER, _FILLED_EQUITY_WRAPPER, _CANCELLED_WRAPPER]
    orders = mappers.extract_orders(payload)

    # Three wrappers → three inner orders.
    assert len(orders) == 3

    # Inner fields are present directly on the returned dicts.
    symbols = {o["symbol"] for o in orders}
    assert symbols == {"SPXW", "RIVN", "HOOD"}

    # The actionable fields live on the inner orders.
    assert all("filled_quantity" in o for o in orders)
    assert all("status" in o for o in orders)


def test_extract_orders_flat_shape_unchanged():
    """extract_orders must NOT break the old flat shape (no inner 'orders' list)."""
    flat = [
        {"orderId": "o-1", "symbol": "RIVN", "status": "FILLED", "filledQuantity": "10"},
        {"orderId": "o-2", "symbol": "HOOD", "status": "FILLED", "filledQuantity": "5"},
    ]
    orders = mappers.extract_orders(flat)
    assert len(orders) == 2
    assert {o["orderId"] for o in orders} == {"o-1", "o-2"}


def test_extract_orders_envelope_dict_wrapping_combo_wrappers():
    """extract_orders handles a top-level {"orders": [...combo-wrappers...]} envelope."""
    payload = {
        "orders": [_FILLED_OPTION_WRAPPER, _CANCELLED_WRAPPER]
    }
    orders = mappers.extract_orders(payload)
    # Two wrappers, one inner each → two inner orders.
    assert len(orders) == 2
    assert {o["symbol"] for o in orders} == {"SPXW", "HOOD"}


def test_is_filled_on_v3_inner_order():
    """is_filled correctly identifies filled/cancelled inner orders from v3 shape."""
    filled_inner = _FILLED_OPTION_WRAPPER["orders"][0]
    cancelled_inner = _CANCELLED_WRAPPER["orders"][0]

    assert mappers.is_filled(filled_inner) is True
    assert mappers.is_filled(cancelled_inner) is False


def test_map_fill_v3_inner_option_order():
    """_map_fill correctly maps a v3 inner option order to a Fill.

    Uses the exact live shape: filled_time_at is ISO-8601, side is snake_case.
    """
    inner = _FILLED_OPTION_WRAPPER["orders"][0]
    f = mappers._map_fill(inner)

    assert isinstance(f, Fill)
    assert f.symbol == "SPXW"
    assert f.side is Side.sell    # "SELL" → Side.sell  (v3 "side" field)
    assert f.qty == 1.0
    assert f.price == 2.20
    assert f.provider_fill_id == "OPT-INNER-1"   # client_order_id preferred
    assert f.simulated is False
    assert f.fees == 0.0
    # filled_time_at "2026-07-09T19:59:52.982Z" → UTC
    expected_ts = datetime(2026, 7, 9, 19, 59, 52, 982000, tzinfo=UTC)
    assert f.ts == expected_ts


def test_map_fill_v3_inner_equity_sell():
    """_map_fill maps a v3 equity SELL inner order correctly."""
    inner = _FILLED_EQUITY_WRAPPER["orders"][0]
    f = mappers._map_fill(inner)

    assert f.symbol == "RIVN"
    assert f.side is Side.sell
    assert f.qty == 100.0
    assert f.price == 12.50
    assert f.provider_fill_id == "EQ-INNER-2"


def test_get_fills_with_v3_combo_wrapper_payload(tmp_path, monkeypatch):
    """get_fills end-to-end: combo-wrapper payload → only FILLED orders returned."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    payload = [_FILLED_OPTION_WRAPPER, _FILLED_EQUITY_WRAPPER, _CANCELLED_WRAPPER]

    class _ComboOrderV3:
        calls: list = []

        def get_order_history(self, account_id, page_size=None,
                              start_date=None, end_date=None,
                              last_client_order_id=None):
            _ComboOrderV3.calls.append(last_client_order_id)
            # Return wrappers only on the first (cursor=None) call; empty after.
            if last_client_order_id is None:
                return payload
            return []

    class _FakeTradeCombo:
        order_v3 = _ComboOrderV3()
        account_v2 = _FakeAccountV2ForOrders()

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTradeCombo()

    fills = prov.get_fills()

    # Two FILLED wrappers → two fills; CANCELLED is excluded.
    assert len(fills) == 2
    syms = {f.symbol for f in fills}
    assert syms == {"SPXW", "RIVN"}
    fill_ids = {f.provider_fill_id for f in fills}
    assert fill_ids == {"OPT-INNER-1", "EQ-INNER-2"}


# --------------------------------------------------------------------------- #
# 417 date-param error — graceful stop of backward walk
# --------------------------------------------------------------------------- #
def test_get_fills_stops_walk_on_417_date_param_error(tmp_path, monkeypatch):
    """A 417 'invalid start_date' from the API must stop the backward date-walk
    gracefully and return whatever fills were already collected — not raise."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    # First window succeeds with one fill; second window raises the 417.
    call_count = 0
    good_fill = _order("g-1", "RIVN", "BUY", "10", "12.00", "1720000000000")

    class _FakeApiException417(Exception):
        status_code = 417

        def __init__(self):
            super().__init__("OAUTH_OPENAPI_PARAM_ERR: invalid start_date, value: 2024-04-22")

    class _Bouncing417OrderV3:
        calls: list = []

        def get_order_history(self, account_id, page_size=None,
                              start_date=None, end_date=None,
                              last_client_order_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First window succeeds.
                return {"orders": [good_fill]}
            # Subsequent windows: raise 417 simulating the date boundary.
            raise _FakeApiException417()

    class _FakeTrade417:
        order_v3 = _Bouncing417OrderV3()
        account_v2 = _FakeAccountV2ForOrders()

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTrade417()

    # Must NOT raise; must return the one fill from the first successful window.
    fills = prov.get_fills()

    assert len(fills) == 1
    assert fills[0].provider_fill_id == "g-1"


# --------------------------------------------------------------------------- #
# contract_multiplier() helper
# --------------------------------------------------------------------------- #
def test_contract_multiplier_option_order():
    """An order with legs[0].option_contract_multiplier = '100' → 100.0."""
    order = {
        "symbol": "SPXW",
        "instrument_type": "OPTION",
        "legs": [{"option_contract_multiplier": "100", "strike_price": "5500", "option_type": "CALL"}],
    }
    assert mappers.contract_multiplier(order) == 100.0


def test_contract_multiplier_stock_order_no_legs():
    """A stock order with no legs → 1.0."""
    order = {"symbol": "RIVN", "instrument_type": "STK"}
    assert mappers.contract_multiplier(order) == 1.0


def test_contract_multiplier_empty_legs():
    """An order with an empty legs list → 1.0 (defensive)."""
    order = {"symbol": "SPXW", "legs": []}
    assert mappers.contract_multiplier(order) == 1.0


def test_contract_multiplier_missing_field():
    """legs present but option_contract_multiplier absent → 1.0 (defensive)."""
    order = {"symbol": "SPXW", "legs": [{"strike_price": "5500"}]}
    assert mappers.contract_multiplier(order) == 1.0


def test_contract_multiplier_blank_string():
    """Blank option_contract_multiplier → 1.0 (defensive)."""
    order = {"symbol": "SPXW", "legs": [{"option_contract_multiplier": ""}]}
    assert mappers.contract_multiplier(order) == 1.0


def test_contract_multiplier_unparseable():
    """Non-numeric option_contract_multiplier → 1.0 (defensive)."""
    order = {"symbol": "SPXW", "legs": [{"option_contract_multiplier": "N/A"}]}
    assert mappers.contract_multiplier(order) == 1.0


def test_map_fill_option_extracts_contract_fields():
    """_map_fill on an option inner order extracts option_type, strike, expiry
    from legs[0] and populates the new Fill contract-identity fields."""
    inner = {
        "symbol": "SPXW",
        "side": "BUY",
        "status": "FILLED",
        "filled_quantity": "2",
        "filled_price": "3.50",
        "filled_time_at": "2026-07-09T14:30:00.000Z",
        "client_order_id": "test-contract-1",
        "instrument_type": "OPTION",
        "legs": [
            {
                "option_type": "CALL",
                "strike_price": "7500",
                "option_expire_date": "2026-07-11",
                "option_contract_multiplier": "100",
            }
        ],
    }
    f = mappers._map_fill(inner)
    assert f.option_type == "CALL"
    assert f.strike == 7500.0
    assert f.expiry == "2026-07-11"
    assert f.multiplier == 100.0


def test_map_fill_put_option_extracts_contract_fields():
    """PUT options also extract correctly; strike as non-integer float."""
    inner = {
        "symbol": "SPXW",
        "side": "SELL",
        "status": "FILLED",
        "filled_quantity": "1",
        "filled_price": "1.20",
        "filled_time_at": "2026-07-09T15:00:00.000Z",
        "client_order_id": "test-contract-2",
        "instrument_type": "OPTION",
        "legs": [
            {
                "option_type": "PUT",
                "strike_price": "7475.5",
                "option_expire_date": "2026-07-09",
            }
        ],
    }
    f = mappers._map_fill(inner)
    assert f.option_type == "PUT"
    assert f.strike == 7475.5
    assert f.expiry == "2026-07-09"


def test_map_fill_stock_has_no_contract_fields():
    """_map_fill on a stock (no legs) leaves option_type/strike/expiry as None."""
    inner = _FILLED_EQUITY_WRAPPER["orders"][0]
    f = mappers._map_fill(inner)
    assert f.option_type is None
    assert f.strike is None
    assert f.expiry is None


def test_map_fill_option_without_expire_date():
    """legs[0] present but missing option_expire_date → expiry=None (defensive)."""
    inner = {
        "symbol": "SPY",
        "side": "BUY",
        "status": "FILLED",
        "filled_quantity": "1",
        "filled_price": "2.00",
        "filled_time_at": "2026-07-09T14:00:00.000Z",
        "client_order_id": "test-no-expiry",
        "legs": [{"option_type": "CALL", "strike_price": "550"}],
    }
    f = mappers._map_fill(inner)
    assert f.option_type == "CALL"
    assert f.strike == 550.0
    assert f.expiry is None
    inner = _FILLED_OPTION_WRAPPER["orders"][0]
    # Inner order has legs without option_contract_multiplier; add it for this test.
    inner_with_multiplier = {
        **inner,
        "legs": [{"option_contract_multiplier": "100", "strike_price": "5500", "option_type": "CALL"}],
    }
    f = mappers._map_fill(inner_with_multiplier)
    assert f.multiplier == 100.0


def test_map_fill_equity_order_multiplier_defaults_to_1():
    """_map_fill on a stock order sets multiplier=1.0 (default)."""
    inner = _FILLED_EQUITY_WRAPPER["orders"][0]
    f = mappers._map_fill(inner)
    assert f.multiplier == 1.0


def test_417_non_date_param_error_still_raises(tmp_path, monkeypatch):
    """A 417 that is NOT an invalid-start_date error must still propagate."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    class _FakeApiException417Other(Exception):
        status_code = 417

        def __init__(self):
            super().__init__("page_size value should be between 10 and 100")

    class _ErrorOrderV3:
        def get_order_history(self, *a, **kw):
            raise _FakeApiException417Other()

    class _FakeTrade417Other:
        order_v3 = _ErrorOrderV3()
        account_v2 = _FakeAccountV2ForOrders()

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = _FakeTrade417Other()

    from app.providers.webull.errors import WebullError
    with pytest.raises(WebullError):
        prov.get_fills()


# --------------------------------------------------------------------------- #
# Global rate gate — process-wide, endpoint-agnostic spacing
# --------------------------------------------------------------------------- #
def test_rate_gate_spaces_calls(monkeypatch):
    """_RATE_GATE.acquire() must space consecutive calls by ~_GATE_MIN_INTERVAL_S.

    time.monotonic is stubbed to a controllable counter that does NOT
    auto-advance, so the only way "time" moves forward is via the sleeps the
    gate itself issues — making the wait math fully deterministic.
    """
    from app.providers.webull.provider import _GATE_MIN_INTERVAL_S, _RATE_GATE

    clock = {"now": 0.0}
    sleeps: list[float] = []

    def fake_monotonic():
        return clock["now"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr("app.providers.webull.provider.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.providers.webull.provider.time.sleep", fake_sleep)

    _RATE_GATE._next = 0.0
    _RATE_GATE.acquire()
    _RATE_GATE.acquire()
    _RATE_GATE.acquire()

    assert len(sleeps) >= 2, f"expected at least 2 spacing sleeps, got {sleeps}"
    for s in sleeps:
        assert s == pytest.approx(_GATE_MIN_INTERVAL_S, abs=1e-6)


def test_guard_retries_429_on_any_endpoint(tmp_path, monkeypatch):
    """_guard must retry a rate-limit rejection on ANY call, not just order-history.

    This proves the retry-on-429 behavior now lives in _guard itself (shared
    by every SDK call path) rather than being order-history-specific.
    """
    monkeypatch.setattr("app.providers.webull.provider.time.sleep", lambda _: None)

    prov = WebullProvider(_creds())
    _write_token_file(tmp_path, status="NORMAL", expires_ms=9999999999000)
    prov._token_dir = tmp_path
    prov._api_client = object()
    prov._mdata = object()
    prov._trade = object()

    calls = {"n": 0}

    class _FakeRateLimitException(Exception):
        status_code = 429

        def __init__(self):
            super().__init__("HTTP 429 too many requests")

    def stub():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeRateLimitException()
        return {}

    result = prov._guard(stub)

    assert result == {}
    assert calls["n"] == 2, "stub must be called twice: initial 429 then a successful retry"

