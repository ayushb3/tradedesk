"""Replay session endpoint (M2).

Contract seam (quant owns the implementation):
    app.indicators.service.compute_replay_session(
        provider, symbol: str, session_date: date, settings
    ) -> ReplaySessionResponse

The returned arrays (indicators + levels) MUST be causal and aligned index-for-
index with candles: value at i uses only candles[0..i]. See models/replay.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import provider_dep, settings_dep
from app.config import Settings
from app.models.replay import (
    DaySoFarResponse,
    RealResult,
    ReplayFill,
    ReplayContractResponse,
    ReplayFillsResponse,
    ReplaySessionResponse,
    ReplayTradeResponse,
)
from app.providers.base import DataProvider

router = APIRouter(tags=["replay"])

# Timeframes the providers can serve (see webull provider _TIMEFRAME_TO_WEBULL
# and the mock provider, which resamples from 1m). Keep in sync with those.
_SUPPORTED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "1d"}

# Guardrails on indicator params so a crafted request can't ask for absurd work.
_MAX_EMA_PERIODS = 6
_MIN_PERIOD = 1
_MAX_PERIOD = 500


def _parse_ema_periods(raw: str | None) -> tuple[int, ...]:
    """Parse a comma-separated EMA-periods string, e.g. "9,21,50,200".

    Falls back to the service default when unset/blank. Raises HTTP 400 on
    malformed or out-of-range values.
    """
    if raw is None or raw.strip() == "":
        from app.indicators.service import DEFAULT_EMA_PERIODS

        return DEFAULT_EMA_PERIODS
    periods: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            p = int(tok)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid ema period: {tok!r}")
        if not (_MIN_PERIOD <= p <= _MAX_PERIOD):
            raise HTTPException(
                status_code=400,
                detail=f"ema period {p} out of range [{_MIN_PERIOD}, {_MAX_PERIOD}]",
            )
        periods.append(p)
    if not periods:
        raise HTTPException(status_code=400, detail="ema_periods must list at least one period")
    if len(periods) > _MAX_EMA_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"at most {_MAX_EMA_PERIODS} ema periods allowed",
        )
    return tuple(periods)


@router.get("/replay/session", response_model=ReplaySessionResponse)
def replay_session(
    symbol: str = Query(...),
    date_: date = Query(..., alias="date"),
    timeframe: str = Query("1m"),
    ema_periods: str | None = Query(None, description="Comma-separated, e.g. 9,21,50,200"),
    rsi_period: int = Query(14, ge=_MIN_PERIOD, le=_MAX_PERIOD),
    or_minutes: int | None = Query(None, ge=1, le=390),
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> ReplaySessionResponse:
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported timeframe {timeframe!r}; "
            f"expected one of {sorted(_SUPPORTED_TIMEFRAMES)}",
        )
    periods = _parse_ema_periods(ema_periods)

    try:
        from app.indicators.service import compute_replay_session
    except ImportError:
        raise HTTPException(status_code=501, detail="indicators service not implemented")
    try:
        return compute_replay_session(
            provider,
            symbol.upper(),
            date_,
            settings,
            timeframe=timeframe,
            ema_periods=periods,
            rsi_period=rsi_period,
            or_minutes=or_minutes,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="indicators service not implemented")


# --------------------------------------------------------------------------- #
# Replay my trade
# --------------------------------------------------------------------------- #
_DEFAULT_LEAD_MINUTES = 15
_MAX_LEAD_MINUTES = 240
_PT = ZoneInfo("America/Los_Angeles")


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


_AFTER_EXIT_MINUTES = 30


def _window_end(trade, head: datetime) -> datetime | None:
    """Real right edge of the x-range (exit + 30 min), revealed only once the
    playhead is at or past the exit. Before that it is None: the exit time must
    never be derivable from anything sent early. The client starts from a fixed
    window that depends only on the entry and extends when this arrives."""
    if trade.close_ts is None or _as_utc(head) < _as_utc(trade.close_ts):
        return None
    return _as_utc(trade.close_ts) + timedelta(minutes=_AFTER_EXIT_MINUTES)


def _real_result(trade, head: datetime) -> RealResult | None:
    """The real trade's net P&L and R (the ledger's one definition), on the SAME
    gate as ``window_end_ts``: None until the playhead is at or past the exit."""
    if _window_end(trade, head) is None or trade.net_pnl is None:
        return None
    return RealResult(net_pnl=trade.net_pnl, r=trade.r_multiple, r_basis=trade.r_basis)


def _load_trade_or_404(trade_id: int):
    from app.ingest.query import get_trade

    trade = get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return trade


def revealed_fills(trade_id: int, playhead: datetime) -> list[ReplayFill]:
    """Server-side gate: only fills with ts <= playhead are ever returned."""
    from app.ingest.query import get_trade_fills

    head = _as_utc(playhead)
    out: list[ReplayFill] = []
    for f in get_trade_fills(trade_id):
        if _as_utc(f.ts) <= head:
            out.append(
                ReplayFill(
                    seq=f.seq,
                    role=f.role,
                    side=f.side,
                    qty=f.qty,
                    price=f.price,
                    ts=f.ts,
                    time_pt=_as_utc(f.ts).astimezone(_PT).strftime("%H:%M:%S PT"),
                )
            )
    return out


@router.get("/replay/trade/{trade_id}", response_model=ReplayTradeResponse)
def replay_trade(
    trade_id: int,
    lead_minutes: int = Query(_DEFAULT_LEAD_MINUTES, ge=0, le=_MAX_LEAD_MINUTES),
    playhead: datetime | None = Query(
        None, description="Reveal fills at or before this time. Default: the start."
    ),
    timeframe: str = Query("1m"),
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> ReplayTradeResponse:
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"unsupported timeframe {timeframe!r}")
    trade = _load_trade_or_404(trade_id)
    start_ts = _as_utc(trade.open_ts) - timedelta(minutes=lead_minutes)
    head = _as_utc(playhead) if playhead is not None else start_ts

    from app.indicators.service import compute_replay_session

    session = compute_replay_session(
        provider, trade.symbol.upper(), trade.session_date, settings, timeframe=timeframe
    )
    start_index = next(
        (i for i, c in enumerate(session.candles) if _as_utc(c.ts) >= start_ts),
        max(len(session.candles) - 1, 0),
    )
    return ReplayTradeResponse(
        trade_id=trade_id,
        symbol=session.symbol,
        lead_minutes=lead_minutes,
        start_ts=start_ts,
        start_index=start_index,
        window_end_ts=_window_end(trade, head),
        real_result=_real_result(trade, head),
        playhead=head,
        session=session,
        fills=revealed_fills(trade_id, head),
    )


@router.get("/replay/trade/{trade_id}/fills", response_model=ReplayFillsResponse)
def replay_trade_fills(
    trade_id: int,
    playhead: datetime = Query(..., description="Reveal fills at or before this time."),
) -> ReplayFillsResponse:
    """Light poll for the UI as the playhead moves (no candles re-sent)."""
    trade = _load_trade_or_404(trade_id)
    head = _as_utc(playhead)
    return ReplayFillsResponse(
        trade_id=trade_id,
        playhead=head,
        fills=revealed_fills(trade_id, head),
        window_end_ts=_window_end(trade, head),
        real_result=_real_result(trade, head),
    )


@router.get("/replay/trade/{trade_id}/contract", response_model=ReplayContractResponse)
def replay_trade_contract(
    trade_id: int,
    lead_minutes: int = Query(_DEFAULT_LEAD_MINUTES, ge=0, le=_MAX_LEAD_MINUTES),
    playhead: datetime | None = Query(
        None, description="Reveal fills at or before this time. Default: the start."
    ),
    provider: DataProvider = Depends(provider_dep),
) -> ReplayContractResponse:
    """Contract mode: the trade's own option premium 1-min bars, plus the stop
    line (same `stop_price` and ledger risk as the trade review). Fills keep the
    server-side playhead gate. When premium bars are unavailable the response
    has `available=false` and no bars, stop or fills."""
    from app.analytics.excursions import stop_price
    from app.desk.rules import load_rules
    from app.ingest.trade_excursions import UNAVAILABLE, fetch_bars, locate_position

    trade = _load_trade_or_404(trade_id)
    start_ts = _as_utc(trade.open_ts) - timedelta(minutes=lead_minutes)
    head = _as_utc(playhead) if playhead is not None else start_ts
    base = dict(
        trade_id=trade_id, lead_minutes=lead_minutes, start_ts=start_ts, playhead=head,
        session_date=trade.session_date, window_end_ts=_window_end(trade, head),
        real_result=_real_result(trade, head),
    )

    bars, contract, reason = fetch_bars(trade_id, provider)
    if bars is None or not bars.bars:
        return ReplayContractResponse(
            available=False,
            reason=reason if contract is None else UNAVAILABLE,
            symbol=contract.descriptor if contract is not None else None,
            **base,
        )

    stop = None
    stop_assumed = False
    try:
        found = locate_position(trade_id, load_rules())
    except ValueError:  # unreadable rules file: replay still works, without a stop line
        found = None
    if found is not None:
        _, _, a = found
        if a.avg_in is not None:
            stop = stop_price(a.avg_in, a.qty_in, a.risk)
            stop_assumed = a.written_max_loss is None

    candles = bars.bars
    start_index = next(
        (i for i, c in enumerate(candles) if _as_utc(c.ts) >= start_ts),
        max(len(candles) - 1, 0),
    )
    return ReplayContractResponse(
        available=True,
        symbol=contract.descriptor,
        start_index=start_index,
        candles=candles,
        stop_price=stop,
        stop_assumed=stop_assumed,
        synthetic=bars.synthetic,
        estimated=bars.synthetic,
        method=bars.method,
        fills=revealed_fills(trade_id, head),
        **base,
    )


@router.get("/replay/trade/{trade_id}/day-so-far", response_model=DaySoFarResponse)
def replay_day_so_far(trade_id: int) -> DaySoFarResponse:
    """Rule-bar state for the replayed trade, filtered on the server so later
    trades never reach the browser. Counts only positions whose last fill is
    strictly before this trade's entry; this trade is never counted. Carries no
    end-of-day flag and no final day net."""
    from app.desk import days as desk_days
    from app.desk.rules import load_rules, window_bounds

    trade = _load_trade_or_404(trade_id)
    base = dict(trade_id=trade_id, session_date=trade.session_date)
    try:
        rules = load_rules()
    except ValueError:  # unreadable rules file: the bar shows dashes
        return DaySoFarResponse(**base)

    entry = _as_utc(trade.open_ts)
    d = desk_days.get_day(trade.session_date, rules)
    net = 0.0
    prior = 0
    own_source = None
    plan_caller = None
    if d is not None:
        for lp, a in zip(d.loaded, d.result.positions):
            if lp.trade_id == trade_id:
                own_source = lp.call_source
                plan_caller = lp.plan.caller if lp.plan is not None else None
                continue
            if not a.closed or a.pnl is None or not a.fills:
                continue
            if max(_as_utc(f.ts) for f in a.fills) < entry:
                net += a.pnl
                prior += 1
    cutoff_end = window_bounds(rules, trade.session_date)[1]
    return DaySoFarResponse(
        **base,
        net_so_far=round(net, 2),
        prior_trades=prior,
        daily_stop=rules.daily_stop,
        daily_target=rules.daily_target,
        cutoff=rules.window.end,
        entry="before" if entry <= cutoff_end else "after",
        call_source=own_source or plan_caller or None,
    )
