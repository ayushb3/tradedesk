"""Pure response -> pydantic mapping functions for the Webull OpenAPI.

This is the seam that matters. The exact JSON shape returned by the Webull
OpenAPI SDK varies by SDK version and endpoint, and (for us) is not exercised by
a live network call, so every mapper here is:

  * **defensive** — it accepts both ``snake_case`` and ``camelCase`` keys and
    both numeric and stringified numbers (the HTTP Data API returns most numbers
    as strings), and
  * **unit-tested** against representative fixtures in ``tests/test_webull.py``.

If a future SDK upgrade changes a field name, fix it here + add a fixture; no
other code needs to change.

Documented shape assumptions (from the Webull OpenAPI HTTP Data / Trade APIs):

  * **Bar** (``get_bars`` -> ``bars[]``): ``open/high/low/close/volume`` plus a
    ``timestamp`` in **epoch milliseconds** (seconds tolerated) and/or an ISO
    ``trade_time``. Bar timestamp is the bar's OPEN time.
  * **Fill/Order** (``list_*_orders`` items): ``symbol``, ``action`` (BUY/SELL),
    a filled quantity + filled price, a fill time, optional commission, and an
    order/fill id. We map one *filled* order line -> one :class:`Fill`.
  * **Position** (``get_account_position`` -> ``holdings[]``): ``symbol``,
    quantity, cost/avg price, optional market value + unrealized P/L.
  * **Account** (``get_account_balance``): an id + a (possibly nested) balance
    block with cash / net-liquidation / buying-power.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.market import Account, Candle, Position
from app.models.trades import Fill, Side

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Small coercion helpers
# --------------------------------------------------------------------------- #
def _first(d: dict, *keys: str, default: Any = None) -> Any:
    """Return the first present, non-None value among ``keys`` (camelCase or
    snake_case), else ``default``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _num(v: Any) -> float | None:
    """Coerce a str/int/float (Webull often stringifies numbers) to float, or
    ``None`` when the value is missing/blank/unparseable."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _req_num(d: dict, *keys: str) -> float:
    """Like :func:`_num` but required; raises ``KeyError`` if absent."""
    v = _num(_first(d, *keys))
    if v is None:
        raise KeyError(f"missing required numeric field (any of {keys!r})")
    return v


def _to_utc(value: Any) -> datetime:
    """Parse a Webull timestamp into a tz-aware UTC datetime.

    Accepts epoch **milliseconds** (the common case), epoch **seconds**, or an
    ISO-8601 string. Millisecond vs second is decided by magnitude.
    """
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().lstrip("-").isdigit()
    ):
        n = float(value)
        # ~1e12 is the ms/s boundary for dates around the year 2001+.
        if abs(n) >= 1e11:
            n /= 1000.0
        return datetime.fromtimestamp(n, tz=UTC)
    # ISO-8601; normalize a trailing 'Z' and assume UTC when naive.
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _side(action: Any) -> Side:
    """Map a Webull order action to our :class:`Side`.

    BUY / BUY_TO_OPEN / BUY_TO_COVER -> buy; SELL / SELL_SHORT / ... -> sell.
    """
    a = str(action or "").upper()
    if a.startswith("BUY"):
        return Side.buy
    if a.startswith("SELL"):
        return Side.sell
    raise ValueError(f"unrecognized Webull order action: {action!r}")


def contract_multiplier(order: dict) -> float:
    """Return the options contract multiplier for an order.

    For options orders the Webull OpenAPI returns the multiplier as a string
    in ``legs[0].option_contract_multiplier`` (e.g. ``"100"``).  Stock orders
    have no ``legs`` field, so we default to 1.0.

    Defensive: any missing / blank / unparseable value → 1.0.
    """
    legs = order.get("legs")
    if not isinstance(legs, list) or not legs:
        return 1.0
    raw = legs[0].get("option_contract_multiplier") if isinstance(legs[0], dict) else None
    result = _num(raw)
    return result if result is not None and result > 0 else 1.0


def _option_fields(order: dict) -> tuple[str | None, float | None, str | None]:
    """Extract (option_type, strike, expiry) from an order's legs[0].

    For stock orders (no legs field) all three return None.  Defensive against
    any missing / malformed leg data — a bad leg value returns None rather than
    raising, so a single garbled field doesn't drop the fill.

    Fields read from legs[0]:
      * ``option_type``  — "CALL" | "PUT" (uppercased for consistency)
      * ``strike_price`` — numeric string → float
      * ``option_expire_date`` — ISO date string, e.g. "2026-07-09"
    """
    legs = order.get("legs")
    if not isinstance(legs, list) or not legs:
        return None, None, None
    leg = legs[0]
    if not isinstance(leg, dict):
        return None, None, None

    raw_type = leg.get("option_type")
    option_type = str(raw_type).upper() if raw_type else None

    raw_strike = leg.get("strike_price")
    strike = _num(raw_strike)  # None when missing/blank/unparseable

    raw_expiry = leg.get("option_expire_date")
    expiry = str(raw_expiry) if raw_expiry else None

    return option_type, strike, expiry



def _map_bar(bar: dict, symbol: str, timeframe: str = "1m") -> Candle:
    """One Webull bar dict -> :class:`Candle` (ts = bar OPEN time, UTC)."""
    ts_raw = _first(bar, "timestamp", "tradeTime", "trade_time", "time")
    if ts_raw is None:
        raise KeyError("bar is missing a timestamp/tradeTime field")
    return Candle(
        symbol=str(_first(bar, "symbol", "ticker", default=symbol)),
        ts=_to_utc(ts_raw),
        open=_req_num(bar, "open", "openPrice", "open_price"),
        high=_req_num(bar, "high", "highPrice", "high_price"),
        low=_req_num(bar, "low", "lowPrice", "low_price"),
        close=_req_num(bar, "close", "closePrice", "close_price"),
        volume=_num(_first(bar, "volume", "vol")) or 0.0,
        timeframe=timeframe,
    )


def _map_fill(order: dict) -> Fill:
    """One *filled* Webull order/execution dict -> :class:`Fill`.

    Handles both the **order_v3 inner order** shape (snake_case fields from the
    live API) and the older camelCase shape:

    * ``side`` / ``action`` / ``orderSide``  → :class:`Side`
    * ``filled_quantity`` / ``filledQuantity`` / ``filledQty``
    * ``filled_price`` / ``avgFilledPrice`` / ``avg_filled_price`` …
    * ``filled_time_at`` (v3 epoch ms) / ``filled_time`` / ``filledTime`` …
    * ``client_order_id`` / ``clientOrderId`` / ``order_id`` / ``orderId``

    Fees come from any of the common commission fields, defaulting to 0.
    """
    ts_raw = _first(
        order,
        "filled_time_at",                    # order_v3 inner order (epoch ms)
        "filledTime", "filled_time",          # old camelCase / snake
        "updateTime", "update_time",
        "createTime",
    )
    if ts_raw is None:
        raise KeyError("order is missing a fill time field")
    fill_id = _first(order, "clientOrderId", "client_order_id", "orderId", "order_id", "id")
    option_type, strike, expiry = _option_fields(order)
    return Fill(
        provider_fill_id=None if fill_id is None else str(fill_id),
        account=_maybe_str(_first(order, "accountId", "account_id", "account")),
        symbol=str(_first(order, "symbol", "ticker")),
        side=_side(_first(order, "side", "action", "orderSide")),   # v3: "side"
        qty=_req_num(order, "filled_quantity", "filledQuantity", "filledQty", "quantity"),
        price=_req_num(
            order,
            "filled_price",                  # order_v3 inner order
            "avgFilledPrice", "avg_filled_price",
            "filledPrice", "filled_price_avg",
            "avgPrice",
        ),
        ts=_to_utc(ts_raw),
        fees=_num(_first(order, "commission", "fee", "fees")) or 0.0,
        multiplier=contract_multiplier(order),
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        simulated=False,
        raw_json=order,
    )


def _map_position(holding: dict) -> Position:
    """One Webull holding dict -> :class:`Position`."""
    return Position(
        symbol=str(_first(holding, "symbol", "ticker")),
        qty=_req_num(holding, "quantity", "position", "qty", "totalQuantity"),
        avg_price=_req_num(
            holding, "costPrice", "cost_price", "avgCost", "avg_cost", "averageCost"
        ),
        market_value=_num(_first(holding, "marketValue", "market_value")),
        unrealized_pnl=_num(
            _first(holding, "unrealizedProfitLoss", "unrealized_profit_loss", "unrealizedPnl")
        ),
    )


def _map_account(data: dict, positions: list[Position] | None = None) -> Account:
    """Webull account-balance dict -> :class:`Account`.

    Balance fields may live at the top level or inside a per-currency block
    (``accountCurrencyAssets`` / ``account_currency_assets``); we look in both.
    """
    balance = _balance_block(data)
    return Account(
        account_id=str(_first(data, "accountId", "account_id", "id", default="")),
        currency=str(_first(balance, "currency", default=_first(data, "currency", default="USD"))),
        cash=_num(
            _first(balance, "cashBalance", "cash_balance", "totalCash", "total_cash", "cash")
        ),
        equity=_num(
            _first(
                balance, "netLiquidation", "net_liquidation", "totalAsset", "total_asset", "equity"
            )
        ),
        buying_power=_num(
            _first(balance, "buyingPower", "buying_power", "dayBuyingPower", "day_buying_power")
        ),
        positions=positions or [],
    )


# --------------------------------------------------------------------------- #
# Response envelope extractors (also tolerant / commented)
# --------------------------------------------------------------------------- #
def extract_bars(payload: Any) -> list[dict]:
    """Pull the flat list of bar dicts out of a get_bars response.

    Webull returns either a top-level ``{"bars": [...]}`` object, a *list* of
    per-instrument objects each carrying ``bars``, or (rarely) a bare list of
    bars. Handle all three.
    """
    if payload is None:
        return []
    if isinstance(payload, dict):
        if "bars" in payload:
            return list(payload["bars"] or [])
        return [payload] if _looks_like_bar(payload) else []
    if isinstance(payload, list):
        out: list[dict] = []
        for item in payload:
            if isinstance(item, dict) and "bars" in item:
                out.extend(item["bars"] or [])
            elif isinstance(item, dict) and _looks_like_bar(item):
                out.append(item)
        return out
    return []


def extract_holdings(payload: Any) -> list[dict]:
    """Pull the list of holding dicts out of a positions response."""
    if isinstance(payload, dict):
        return list(_first(payload, "holdings", "positions", "items", default=[]) or [])
    if isinstance(payload, list):
        return [h for h in payload if isinstance(h, dict)]
    return []


def extract_orders(payload: Any) -> list[dict]:
    """Pull and FLATTEN the list of order dicts out of an orders response.

    order_v3 returns a list of **combo-wrapper** objects shaped like::

        {
          "client_order_id": "…",
          "combo_type": "NORMAL",
          "combo_order_id": "…",
          "orders": [
            {
              "symbol": "SPXW", "side": "BUY", "status": "FILLED",
              "filled_quantity": "1", "filled_price": "2.35",
              "filled_time_at": 1720000000000,
              "client_order_id": "…", "order_id": "…", …
            }
          ]
        }

    The actionable data (symbol, status, filled_quantity, …) lives on the
    **inner** order objects, not the wrapper. This function flattens each
    wrapper by yielding its ``orders`` children individually. If an item has no
    inner ``orders`` list — i.e. it already looks like a flat order (old SDK
    shape or a direct dict) — it is yielded as-is. This keeps existing tests
    green without any fixture changes.

    All three envelope shapes are handled:

    * ``{"orders": [...]}`` top-level dict (old SDK shape or API envelope)
    * A flat list of order dicts (old flat shape)
    * A list of combo-wrapper dicts (order_v3 live shape) — flattened here
    """
    if payload is None:
        return []

    # ---- unwrap a top-level envelope dict first -------------------------
    if isinstance(payload, dict):
        raw = _first(payload, "orders", "items", "data", default=None)
        if raw is None:
            return []
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    elif isinstance(payload, list):
        items = [o for o in payload if isinstance(o, dict)]
    else:
        return []

    # ---- flatten combo-wrappers -----------------------------------------
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = item.get("orders")
        if isinstance(inner, list) and inner:
            # Combo-wrapper: yield each inner order.  Propagate the wrapper's
            # client_order_id as a fallback id so dedupe still works when the
            # inner order is missing its own id.
            wrapper_coid = item.get("client_order_id") or item.get("clientOrderId")
            for o in inner:
                if isinstance(o, dict):
                    if wrapper_coid and not o.get("client_order_id") and not o.get("clientOrderId"):
                        o = {**o, "client_order_id": wrapper_coid}
                    out.append(o)
        else:
            # Flat order (old shape or single-order wrapper with empty list).
            out.append(item)
    return out


def order_cursor_id(order: dict) -> str | None:
    """The order id used to page ``get_order_history`` via ``last_client_order_id``.

    order_v3 (the current namespace) paginates via ``last_client_order_id``, so
    we prefer the ``clientOrderId`` / ``client_order_id`` field to stay
    consistent with what the server uses as a cursor. Fall back to ``orderId``
    / ``order_id`` / ``id`` when the client-order-id field is absent (shouldn't
    happen on v3, but defensive).

    Also used as the dedupe key in :func:`get_fills` and as ``provider_fill_id``
    in :func:`_map_fill` — all three must agree on the same field.
    """
    oid = _first(order, "clientOrderId", "client_order_id", "orderId", "order_id", "id")
    return None if oid is None else str(oid)


def is_filled(order: dict) -> bool:
    """True when an order line carries an actual execution worth recording."""
    status = str(_first(order, "status", "orderStatus", default="")).upper()
    if status in {"CANCELLED", "CANCELED", "FAILED", "PENDING", "REJECTED"}:
        return False
    return (_num(_first(order, "filledQuantity", "filled_quantity", "filledQty")) or 0.0) > 0


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _maybe_str(v: Any) -> str | None:
    return None if v is None else str(v)


_BAR_KEYS = {"open", "close", "high", "low", "openPrice", "closePrice"}


def _looks_like_bar(d: dict) -> bool:
    return bool(_BAR_KEYS & set(d.keys()))


def _balance_block(data: dict) -> dict:
    """Return the dict that actually holds cash/equity/buying-power.

    Prefer a nested per-currency asset block when present, else the top level.
    """
    assets = _first(data, "accountCurrencyAssets", "account_currency_assets", "currencyAssets")
    if isinstance(assets, list) and assets:
        return assets[0]
    if isinstance(assets, dict):
        return assets
    return data
