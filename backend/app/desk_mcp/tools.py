"""Desk MCP read tools: thin wrappers over ``app.desk`` returning JSON-able dicts.

Every result carries ``tz`` and ``as_of`` (newest fill time, UTC). Times are Pacific
and labelled "PT" in the ``*_pt`` fields. Nothing here touches the network; positions
are identified by ``trade_id`` / ``trade_key`` only (never an account or order id).
Response shapes come from ``app.desk.serialize``, shared with the HTTP routes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import plans as plans_t
from app.db.schema import trades as trades_t
from app.desk import days as desk_days
from app.desk import serialize
from app.desk.rules import Rules, load_rules

from . import mock


@dataclass
class Ctx:
    mode: str  # "mock" | "real"
    db_path: str
    engine: Engine
    rules: Rules


def is_mock_mode() -> bool:
    """Mock only when the environment explicitly says so. Settings defaults to mock
    for the web app, so it is deliberately not consulted for this decision."""
    return os.environ.get("TRADEDESK_MODE", "").strip().lower() == "mock"


def build_ctx() -> Ctx:
    """TRADEDESK_MODE=mock in the environment -> synthetic fixture DB + placeholder
    rules. Otherwise REAL data: settings.db_path + the personal rules file."""
    if is_mock_mode():
        path = mock.ensure_mock_db()
        engine = get_engine(str(path))
        init_db(engine)
        return Ctx("mock", str(path), engine, mock.mock_rules())
    db_path = get_settings().db_path
    engine = get_engine(db_path)
    init_db(engine)
    return Ctx("real", db_path, engine, load_rules())


def _as_of_iso(ctx: Ctx) -> str | None:
    as_of = desk_days.as_of(ctx.engine)
    return as_of.isoformat().replace("+00:00", "Z") if as_of else None


def _meta(ctx: Ctx) -> dict:
    return {"tz": ctx.rules.timezone, "as_of": _as_of_iso(ctx)}


def _parse_date(value: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def _json(model) -> dict:
    return model.model_dump(mode="json")


# ----------------------------------------------------------------------- tools

def status(ctx: Ctx) -> dict:
    """DB path, mode, last import (as_of), and row counts."""
    with ctx.engine.connect() as conn:
        n_fills = conn.execute(
            select(func.count()).select_from(fills_t).where(fills_t.c.simulated.is_(False))
        ).scalar() or 0
        n_trades = conn.execute(
            select(func.count()).select_from(trades_t)
            .where(trades_t.c.simulated.is_(False)).where(trades_t.c.context != "training")
        ).scalar() or 0
        n_plans = conn.execute(select(func.count()).select_from(plans_t)).scalar() or 0
    _, days = desk_days.list_days(ctx.rules, ctx.engine)
    meta = _meta(ctx)
    return {
        **meta, "mode": ctx.mode, "db_path": str(Path(ctx.db_path)),
        "last_import": meta["as_of"],
        "counts": {"fills": n_fills, "trades": n_trades, "days": len(days), "plans": n_plans},
    }


def rules(ctx: Ctx) -> dict:
    return {**ctx.rules.model_dump(mode="json"), **_meta(ctx)}


def list_days(ctx: Ctx) -> dict:
    rules_, days = desk_days.list_days(ctx.rules, ctx.engine)
    return _json(serialize.days_response(rules_, days, desk_days.as_of(ctx.engine)))


def get_day(ctx: Ctx, date: str) -> dict:
    day = _parse_date(date)
    d = desk_days.get_day(day, ctx.rules, ctx.engine)
    if d is None:
        raise ValueError(f"no trades or day fields for {day}")
    mode = _attach_excursions(ctx, [d])
    return _json(serialize.day_detail(day, d, ctx.rules, desk_days.as_of(ctx.engine), mode))


def get_trade(ctx: Ctx, id: int) -> dict:
    """One position (with fills) from its day, plus its full plan card if matched."""
    with ctx.engine.connect() as conn:
        session_date = conn.execute(
            select(trades_t.c.session_date).where(trades_t.c.id == id)
            .where(trades_t.c.simulated.is_(False)).where(trades_t.c.context != "training")
        ).scalar()
    if session_date is None:
        raise ValueError(f"no trade with id {id}")
    day = (
        session_date if isinstance(session_date, date_type)
        else date_type.fromisoformat(str(session_date))
    )
    d = desk_days.get_day(day, ctx.rules, ctx.engine)
    pair = None
    if d is not None:
        pair = next(
            ((lp, a) for lp, a in zip(d.loaded, d.result.positions) if lp.trade_id == id), None
        )
    if pair is None:
        raise ValueError(f"no trade with id {id}")
    lp, a = pair
    full_plan = next((p for p in d.plans if lp.plan and p.id == lp.plan.id), None)
    return {
        **_meta(ctx), "date": day.isoformat(),
        "trade": _json(serialize.position_out(lp, a, ctx.rules, None, d.rule_results.trades.get(id))),
        "plan": _json(full_plan) if full_plan else None,
        "excursions": _excursions(ctx, id, lp, a),
    }


def _excursions(ctx: Ctx, trade_id: int, lp, a) -> dict:
    """MAE/MFE, stop line and post-exit path for one position.

    No network from here. Mock mode computes from the deterministic mock provider
    (nothing cached or written); real mode only reads option bars already cached
    by the app, and says "option bars unavailable" when there are none.
    """
    from app.ingest.option_bars import OptionBars, get_cached_option_bars_for_trade
    from app.ingest.query import get_trade_option_contract
    from app.ingest.trade_excursions import UNAVAILABLE, build_trade_excursions

    contract = get_trade_option_contract(trade_id, engine=ctx.engine)
    bars: OptionBars | None = None
    reason = UNAVAILABLE
    if contract is None:
        reason = "not an option trade"
    elif ctx.mode == "mock":
        from app.ingest.option_bars import get_option_bars_for_trade
        from app.providers.mock import MockProvider

        bars = get_option_bars_for_trade(
            trade_id, MockProvider(get_settings()), "1m", ctx.engine, use_cache=False
        )
    else:
        bars = get_cached_option_bars_for_trade(trade_id, "1m", ctx.engine)
        reason = f"{UNAVAILABLE} (not cached; open the trade review in the app to load them)"
    res = build_trade_excursions(
        lp, a, ctx.rules, bars,
        contract_label=contract.descriptor if contract else None, reason=reason,
    )
    return _json(res)


def _attach_excursions(ctx: Ctx, days) -> str:
    """Attach excursion facts (#52) to ``days``; returns the bars mode used.

    No network from here: mock mode uses the deterministic mock provider (nothing
    cached or written), real mode reads only option bars already cached.
    """
    from app.ingest.excursion_rollup import attach_excursions

    if ctx.mode == "mock":
        from app.providers.mock import MockProvider

        attach_excursions(days, ctx.rules, MockProvider(get_settings()), ctx.engine, "mock")
        return "mock"
    attach_excursions(days, ctx.rules, None, ctx.engine, "cache")
    return "cache"


def patterns(ctx: Ctx, date_from: str | None = None, date_to: str | None = None) -> dict:
    d_from = _parse_date(date_from) if date_from else None
    d_to = _parse_date(date_to) if date_to else None
    rules_, a, days = desk_days.patterns(d_from, d_to, ctx.rules, ctx.engine)
    mode = _attach_excursions(ctx, days)
    return _json(serialize.patterns_response(
        rules_, a, days, d_from, d_to, desk_days.as_of(ctx.engine), mode))


def line_status(ctx: Ctx) -> dict:
    ls = desk_days.line_status(rules=ctx.rules, engine=ctx.engine)
    return _json(serialize.line_response(ls, desk_days.as_of(ctx.engine)))
