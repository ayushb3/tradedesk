"""Attach per-trade excursion facts (#52) to desk days and roll them up.

Glue only: bars come from ``trade_excursions.fetch_bars`` (same three modes as the
trade review: "provider" | "mock" | "cache"), the math is ``app.analytics.excursions``.
R is the ledger's (``PositionAnalysis.risk``); nothing is redefined here.

A closed trade is *measured* when option bars cover its hold. Trades without bars
are skipped (reported as ``n_skipped``) and never counted in any figure.

In "mock" mode the bars are a pure function of the contract, the session day and
the fills, so each position's fact is memoized per engine under exactly those
inputs (plus the ledger numbers it reads). A day view then synthesizes premium bars
once per position per process instead of on every load. "provider" / "cache" facts
depend on the bar cache's contents and are always recomputed.
"""
from __future__ import annotations

import weakref

from sqlalchemy.engine import Engine

from app.analytics.excursions import (
    EXITED_EARLY_WINDOW_MIN,
    ExcursionFact,
    ExcursionRollup,
    beyond_exit_r,
    compute_excursions,
    rollup_excursions,
)
from app.desk.rules import Rules
from app.ingest.trade_excursions import fetch_bars

_MEMO_MODES = frozenset({"mock"})
_MEMO_MAX = 20_000
# engine -> {position key -> fact (None = not measurable)}. Weak so test engines go away.
_facts: weakref.WeakKeyDictionary[Engine, dict] = weakref.WeakKeyDictionary()


def clear_fact_memo() -> None:
    """Forget memoized facts (for callers that swap the bar source underneath)."""
    _facts.clear()


def _fact_key(day, lp, a, provider, bars_mode: str) -> tuple:
    """Everything ``fact_for_position`` reads, for a mode whose bars are deterministic."""
    p = lp.position
    return (
        bars_mode, type(provider).__name__, getattr(provider, "cache_source", None),
        day, lp.trade_id, lp.label, p.ticker, p.type, p.strike, p.expiry,
        tuple((f.side, f.qty, f.px, f.ts) for f in a.fills),
        a.closed, a.pnl, a.avg_in, a.qty_in, a.risk,
    )


def fact_for_position(
    lp, a, provider=None, engine: Engine | None = None, bars_mode: str = "cache"
) -> ExcursionFact | None:
    """The excursion fact for one closed position, or None when it cannot be measured."""
    sells = [f for f in a.fills if f.side == "S"]
    if not a.closed or a.pnl is None or a.avg_in is None or not a.fills or not sells:
        return None
    bars, contract, _ = fetch_bars(lp.trade_id, provider, engine, bars_mode)
    if contract is None or bars is None or not bars.bars:
        return None
    last = sells[-1]
    ex = compute_excursions(
        bars.bars, avg_in=a.avg_in, qty=a.qty_in, risk=a.risk,
        open_ts=a.fills[0].ts, close_ts=last.ts,
        fills=[(f.ts, f.px) for f in a.fills],  # same bounded, wick-trimmed hold as the review
    )
    if ex.hold is None:
        return None
    return ExcursionFact(
        trade_id=lp.trade_id, pnl=a.pnl, risk=a.risk,
        crossed_stop=ex.hold.crossed_stop, drawdown_minutes=ex.hold.drawdown_minutes,
        beyond_exit_r=beyond_exit_r(
            bars.bars, exit_px=last.px, qty=a.qty_in, risk=a.risk, exit_ts=last.ts,
            window_min=EXITED_EARLY_WINDOW_MIN,
        ),
    )


def attach_excursions(
    days, rules: Rules, provider=None, engine: Engine | None = None, bars_mode: str = "cache"
) -> None:
    """Fill ``DayData.excursions`` in place for every closed position of ``days``."""
    for d in days:
        eng = engine or d.engine
        memo = None
        if bars_mode in _MEMO_MODES and eng is not None:
            memo = _facts.setdefault(eng, {})
            if len(memo) > _MEMO_MAX:
                memo.clear()
        d.excursions = {}
        for lp, a in zip(d.loaded, d.result.positions):
            if memo is None:
                fact = fact_for_position(lp, a, provider, eng, bars_mode)
            else:
                key = _fact_key(d.result.day, lp, a, provider, bars_mode)
                if key in memo:
                    fact = memo[key]
                else:
                    fact = memo[key] = fact_for_position(lp, a, provider, eng, bars_mode)
            if fact is not None:
                d.excursions[lp.trade_id] = fact
        d.excursions_attached = True


def closed_count(days) -> int:
    return sum(1 for d in days for a in d.result.positions if a.closed)


def rollup_for(days) -> ExcursionRollup:
    """Rollup over the attached facts of ``days`` (n_trades = closed positions)."""
    facts = [f for d in days for f in d.excursions.values()]
    return rollup_excursions(facts, closed_count(days))
