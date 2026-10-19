"""Assemble a trade's excursions (#51) from the desk ledger + option bars.

Glue between three existing pieces, adding no new definitions:

* ``app.desk.days`` / ``app.desk.ledger``: the position, its fills and ``risk``
  (written max loss, else cost x ``default_risk_pct``), the same number behind R.
* ``app.ingest.option_bars``: premium bars through the provider seam (cached).
* ``app.analytics.excursions``: the pure MAE/MFE / stop / post-exit math.

Read-only. The three ``bars`` modes let each caller say how far it may reach:

* ``"provider"``: the app's normal path (provider, then BS fallback, cached).
* ``"mock"``: provider call with no cache read/write, for the MCP in mock mode.
* ``"cache"``: only bars already cached; never calls a provider.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Engine

from app.analytics.excursions import compute_excursions
from app.desk import days as desk_days
from app.desk.ledger import PositionAnalysis
from app.desk.rules import Rules
from app.ingest.option_bars import (
    OptionBars,
    get_cached_option_bars_for_trade,
    get_option_bars_for_trade,
)
from app.ingest.query import get_trade, get_trade_option_contract
from app.models.review import (
    ExcursionFill,
    HoldExcursionOut,
    PostExitWindowOut,
    TradeExcursionsResponse,
)

UNAVAILABLE = "option bars unavailable"


def locate_position(
    trade_id: int, rules: Rules, engine: Engine | None = None
) -> tuple[date, desk_days.LoadedPosition, PositionAnalysis] | None:
    """The desk position (and its analysis) for a trade id, or None."""
    trade = get_trade(trade_id, engine=engine)
    if trade is None:
        return None
    d = desk_days.get_day(trade.session_date, rules, engine)
    if d is None:
        return None
    for lp, a in zip(d.loaded, d.result.positions):
        if lp.trade_id == trade_id:
            return trade.session_date, lp, a
    return None


def _pt(ts, rules: Rules) -> str:
    return desk_days.pt_clock(ts, rules)


def build_trade_excursions(
    lp, a: PositionAnalysis, rules: Rules, bars: OptionBars | None,
    *, contract_label: str | None = None, reason: str | None = None,
) -> TradeExcursionsResponse:
    fills = [
        ExcursionFill(side=f.side, qty=f.qty, px=f.px, ts=f.ts, ts_pt=_pt(f.ts, rules))
        for f in a.fills
    ]
    base = dict(
        trade_id=lp.trade_id, status="closed" if a.closed else "open",
        avg_in=a.avg_in, qty=a.qty_in, risk=a.risk,
        risk_source="written_max_loss" if a.written_max_loss is not None else "default_risk_pct",
        contract=contract_label or lp.label, fills=fills,
    )
    if bars is None or not bars.bars or a.avg_in is None or not a.fills:
        return TradeExcursionsResponse(
            available=False, reason=reason or UNAVAILABLE, **base
        )

    sells = [f for f in a.fills if f.side == "S"]
    close_ts = sells[-1].ts if a.closed and sells else None
    ex = compute_excursions(
        bars.bars, avg_in=a.avg_in, qty=a.qty_in, risk=a.risk,
        open_ts=a.fills[0].ts, close_ts=close_ts,
    )
    hold = None
    if ex.hold is not None:
        h = ex.hold
        hold = HoldExcursionOut(
            mae_usd=h.mae_usd, mfe_usd=h.mfe_usd, mae_r=h.mae_r, mfe_r=h.mfe_r,
            mae_per_contract=h.mae_per_contract, mfe_per_contract=h.mfe_per_contract,
            mae_ts=h.mae_ts, mfe_ts=h.mfe_ts,
            mae_ts_pt=_pt(h.mae_ts, rules) if h.mae_ts else None,
            mfe_ts_pt=_pt(h.mfe_ts, rules) if h.mfe_ts else None,
            drawdown_minutes=h.drawdown_minutes, crossed_stop=h.crossed_stop,
            crossed_stop_at=h.crossed_stop_at,
            crossed_stop_at_pt=_pt(h.crossed_stop_at, rules) if h.crossed_stop_at else None,
        )
    if hold is None:
        return TradeExcursionsResponse(
            available=False, reason="option bars do not cover the hold", **base
        )
    return TradeExcursionsResponse(
        available=True, estimated=bars.synthetic, method=bars.method,
        stop_price=ex.stop_price, hold=hold,
        post_exit=[
            PostExitWindowOut(
                minutes=w.minutes, best_r=w.best_r, worst_r=w.worst_r,
                bars=w.bars, complete=w.complete,
            )
            for w in ex.post_exit
        ],
        **base,
    )


def trade_excursions(
    trade_id: int, rules: Rules, provider=None, engine: Engine | None = None,
    bars_mode: str = "provider",
) -> TradeExcursionsResponse | None:
    """Excursions for a trade id; None when the trade is unknown to the desk."""
    found = locate_position(trade_id, rules, engine)
    if found is None:
        return None
    _, lp, a = found
    bars, contract, reason = fetch_bars(trade_id, provider, engine, bars_mode)
    if contract is None:
        return build_trade_excursions(lp, a, rules, None, reason="not an option trade")
    return build_trade_excursions(
        lp, a, rules, bars, contract_label=contract.descriptor, reason=reason
    )


def fetch_bars(
    trade_id: int, provider=None, engine: Engine | None = None, bars_mode: str = "provider"
):
    """(bars, contract, reason) for a trade; ``bars`` None when unavailable.

    ``bars_mode`` as in the module docstring. ``contract`` None = not an option trade.
    """
    contract = get_trade_option_contract(trade_id, engine=engine)
    if contract is None:
        return None, None, "not an option trade"
    bars: OptionBars | None
    if bars_mode == "cache" or provider is None:
        bars = get_cached_option_bars_for_trade(trade_id, "1m", engine)
        reason = f"{UNAVAILABLE} (not cached)"
    else:
        try:
            bars = get_option_bars_for_trade(
                trade_id, provider, "1m", engine, use_cache=(bars_mode != "mock")
            )
        except Exception:  # a provider outage must not break the review
            bars = None
        reason = UNAVAILABLE
    return bars, contract, reason
