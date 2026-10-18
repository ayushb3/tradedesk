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
    ReplayFill,
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
    _load_trade_or_404(trade_id)
    head = _as_utc(playhead)
    return ReplayFillsResponse(
        trade_id=trade_id, playhead=head, fills=revealed_fills(trade_id, head)
    )
