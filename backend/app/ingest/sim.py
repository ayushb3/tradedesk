"""Simulated (replay) trade persistence.

`save_sim_trade` turns a `SimTradeCreate` payload (entry/exit legs from the
bar-replay UI) into persisted `fills` + `trades` + `trade_fills` rows and returns
a fully-populated `Trade`. `list_sim_trades` reads them back.

Money math is done in floats and rounded to 2–4 dp. Timestamps in the payload
are ISO-8601 strings (a trailing `Z` is accepted); they are normalized to naive
UTC `datetime` for storage, matching the `DateTime` columns in `schema.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import trade_fills as trade_fills_t
from app.db.schema import trades as trades_t
from app.models.trades import Direction, Side, Trade, TradeStatus, SimTradeCreate


def _parse_ts(value) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a `Z` suffix) to naive UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _weighted_avg(legs: list[dict]) -> tuple[float, float]:
    """Return (total_qty, qty-weighted avg price) for a list of legs."""
    total_qty = sum(float(l["qty"]) for l in legs)
    if total_qty == 0:
        return 0.0, 0.0
    notional = sum(float(l["qty"]) * float(l["price"]) for l in legs)
    return total_qty, notional / total_qty


def save_sim_trade(payload: SimTradeCreate, engine: Engine | None = None) -> Trade:
    """Compute round-trip metrics for a sim trade, persist it, return the Trade."""
    engine = engine or get_engine()
    init_db(engine)

    entries = list(payload.entries)
    exits = list(payload.exits)
    if not entries:
        raise ValueError("sim trade requires at least one entry")

    entry_ts = sorted(_parse_ts(l["ts"]) for l in entries)
    exit_ts = sorted(_parse_ts(l["ts"]) for l in exits) if exits else []

    entry_qty, avg_entry = _weighted_avg(entries)
    exit_qty, avg_exit_raw = _weighted_avg(exits) if exits else (0.0, None)

    qty = entry_qty
    avg_entry = round(avg_entry, 4)
    avg_exit = round(avg_exit_raw, 4) if exits else None

    long = payload.direction == Direction.long

    gross_pnl = net_pnl = pnl_pct = r_multiple = pnl_per_share = None
    if avg_exit is not None:
        if long:
            pnl_per_share = avg_exit - avg_entry
        else:
            pnl_per_share = avg_entry - avg_exit
        # Sim/replay assumes multiplier = 1 (equity replay; no options contracts).
        gross_pnl = round(pnl_per_share * qty, 2)
        net_pnl = round(gross_pnl - 0.0, 2)  # fees are 0 for sim
        pnl_pct = round((pnl_per_share / avg_entry) * 100, 4) if avg_entry else None

    if payload.stop_price is not None and pnl_per_share is not None:
        risk_per_share = abs(avg_entry - payload.stop_price)
        r_multiple = round(pnl_per_share / risk_per_share, 4) if risk_per_share else None

    open_ts = entry_ts[0]
    close_ts = exit_ts[-1] if exit_ts else None
    hold_time_s = int((close_ts - open_ts).total_seconds()) if close_ts else None

    status = TradeStatus.closed if exit_qty >= entry_qty and exits else TradeStatus.open

    entry_side = Side.buy if long else Side.sell
    exit_side = Side.sell if long else Side.buy

    with engine.begin() as conn:
        # Insert the simulated fills, preserving order via seq.
        fill_ids: list[int] = []
        ordered: list[tuple[dict, Side]] = [(l, entry_side) for l in entries] + [
            (l, exit_side) for l in exits
        ]
        for leg, side in ordered:
            res = conn.execute(
                fills_t.insert().values(
                    provider_fill_id=None,
                    account=None,
                    symbol=payload.symbol,
                    side=side.value,
                    qty=float(leg["qty"]),
                    price=float(leg["price"]),
                    ts_utc=_parse_ts(leg["ts"]),
                    fees=0.0,
                    simulated=True,
                    raw_json=None,
                )
            )
            fill_ids.append(int(res.inserted_primary_key[0]))

        res = conn.execute(
            trades_t.insert().values(
                symbol=payload.symbol,
                direction=payload.direction.value,
                open_ts_utc=open_ts,
                close_ts_utc=close_ts,
                session_date=payload.session_date,
                qty=qty,
                avg_entry=avg_entry,
                avg_exit=avg_exit,
                gross_pnl=gross_pnl,
                fees=0.0,
                net_pnl=net_pnl,
                pnl_pct=pnl_pct,
                stop_price=payload.stop_price,
                r_multiple=r_multiple,
                hold_time_s=hold_time_s,
                status=status.value,
                simulated=True,
            )
        )
        trade_id = int(res.inserted_primary_key[0])

        for seq, fill_id in enumerate(fill_ids):
            conn.execute(
                trade_fills_t.insert().values(
                    trade_id=trade_id, fill_id=fill_id, seq=seq
                )
            )

    return Trade(
        id=trade_id,
        symbol=payload.symbol,
        direction=payload.direction,
        open_ts=open_ts,
        close_ts=close_ts,
        session_date=payload.session_date,
        qty=qty,
        avg_entry=avg_entry,
        avg_exit=avg_exit,
        gross_pnl=gross_pnl,
        fees=0.0,
        net_pnl=net_pnl,
        pnl_pct=pnl_pct,
        stop_price=payload.stop_price,
        r_multiple=r_multiple,
        hold_time_s=hold_time_s,
        status=status,
        simulated=True,
    )


def _row_to_trade(row) -> Trade:
    m = row._mapping
    return Trade(
        id=m["id"],
        symbol=m["symbol"],
        direction=Direction(m["direction"]),
        open_ts=m["open_ts_utc"],
        close_ts=m["close_ts_utc"],
        session_date=m["session_date"],
        qty=m["qty"],
        avg_entry=m["avg_entry"],
        avg_exit=m["avg_exit"],
        gross_pnl=m["gross_pnl"],
        fees=m["fees"],
        net_pnl=m["net_pnl"],
        pnl_pct=m["pnl_pct"],
        stop_price=m["stop_price"],
        r_multiple=m["r_multiple"],
        hold_time_s=m["hold_time_s"],
        status=TradeStatus(m["status"]),
        simulated=bool(m["simulated"]),
    )


def list_sim_trades(engine: Engine | None = None) -> list[Trade]:
    """Return all simulated trades (tags/enrichment empty for now)."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            select(trades_t)
            .where(trades_t.c.simulated.is_(True))
            .order_by(trades_t.c.id)
        ).fetchall()
    return [_row_to_trade(r) for r in rows]
