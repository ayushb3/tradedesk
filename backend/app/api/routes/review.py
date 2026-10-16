"""Trade review + journal day-drill-in endpoints (M7a).

Two additive, read-only endpoints feeding the Trade Review page and the
calendar day-drill-in (CONTRACT §1). Both resolve in mock mode with no creds.

    GET /api/trades/{trade_id}/review   -> TradeReviewResponse
    GET /api/journal/days/{date}/trades -> DayTradesResponse

Share trades only in M7a: the option/premium fields are stubbed null so M7b can
extend the schema without a breaking change. No provider changes, no new
indicators, no order-entry — this router only reads.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.analytics.options import compute_excursion
from app.analytics.review import downsample_closes
from app.api.deps import provider_dep
from app.ingest.option_bars import get_option_bars_for_trade
from app.ingest.query import (
    get_trade,
    get_trade_fills,
    get_trade_option_contract,
    list_trades,
)
from app.models.market import Candle
from app.models.review import (
    DayTradeCard,
    DayTradesResponse,
    Excursion,
    OptionBarsResponse,
    ReviewOption,
    TradeReviewResponse,
    UnderlyingDescriptor,
)
from app.models.trades import Direction
from app.providers.base import DataProvider
from app.providers.webull.errors import WebullError

router = APIRouter(tags=["review"])


def _excursion(
    bars: list[Candle], trade, basis: str, estimated: bool
) -> Excursion | None:
    """Build an Excursion (MFE/MAE) readout from a candle series, or None."""
    if trade.avg_entry is None:
        return None
    res = compute_excursion(
        bars,
        entry_price=trade.avg_entry,
        is_long=trade.direction == Direction.long,
        open_ts=trade.open_ts,
        close_ts=trade.close_ts,
    )
    if res is None:
        return None
    mfe, mae, mfe_pct, mae_pct = res
    return Excursion(
        mfe=mfe, mae=mae, mfe_pct=mfe_pct, mae_pct=mae_pct,
        basis=basis, estimated=estimated,
    )


@router.get("/trades/{trade_id}/review", response_model=TradeReviewResponse)
def trade_review(
    trade_id: int, provider: DataProvider = Depends(provider_dep)
) -> TradeReviewResponse:
    """Full review payload: hydrated trade + ordered fills + underlying chart
    descriptor. For option trades (M7b) the option block, premium bars, and
    MFE/MAE are populated; share trades get a null option block and MFE/MAE
    computed from the underlying."""
    trade = get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")

    fills = get_trade_fills(trade_id)
    contract = get_trade_option_contract(trade_id)

    option: ReviewOption | None = None
    premium_bars: list[Candle] | None = None
    excursion: Excursion | None = None

    if contract is not None:
        ob = get_option_bars_for_trade(trade_id, provider, "1m")
        if ob is not None:
            premium_bars = ob.bars
            option = ReviewOption(
                contract_key=contract.contract_key,
                descriptor=contract.descriptor,
                option_type=contract.option_type,
                strike=contract.strike,
                expiry=contract.expiry,
                synthetic=ob.synthetic,
                method=ob.method,
            )
            excursion = _excursion(
                ob.bars, trade, basis="premium", estimated=ob.synthetic
            )
    else:
        # Share trade: MFE/MAE from the underlying over the hold window.
        try:
            underlying = provider.get_candles(trade.symbol, trade.session_date, "1m")
        except WebullError:
            # A symbol the provider can't fetch (e.g. an index-option root like
            # SPXW as US_STOCK -> 417) must not 502 the cockpit. Degrade to no
            # excursion rather than escalating a decorative fetch failure.
            underlying = []
        excursion = _excursion(
            underlying, trade, basis="underlying", estimated=False
        )

    return TradeReviewResponse(
        trade=trade,
        fills=fills,
        underlying=UnderlyingDescriptor(
            symbol=trade.symbol,
            session_date=trade.session_date.isoformat(),
        ),
        option=option,
        premium_bars=premium_bars,
        excursion=excursion,
    )


@router.get("/trades/{trade_id}/option-bars", response_model=OptionBarsResponse)
def trade_option_bars(
    trade_id: int,
    timeframe: str = "1m",
    provider: DataProvider = Depends(provider_dep),
) -> OptionBarsResponse:
    """Premium OHLCV bars for an option trade's contract (M7b).

    Provider-supplied bars are returned verbatim (``method="provider"``); when
    the provider has no option-bar source the server-side Black-Scholes fallback
    anchored to the trade's fills is used (``method="bs_interp"``,
    ``synthetic=true``). Results are cached in ``option_bars_cache``. 404 if the
    trade is unknown or is a share trade (no option contract)."""
    trade = get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    ob = get_option_bars_for_trade(trade_id, provider, timeframe)
    if ob is None:
        raise HTTPException(status_code=404, detail="trade has no option contract")
    return OptionBarsResponse(bars=ob.bars, synthetic=ob.synthetic, method=ob.method)


@router.get("/journal/days/{day}/trades", response_model=DayTradesResponse)
def day_trades(
    day: date, provider: DataProvider = Depends(provider_dep)
) -> DayTradesResponse:
    """That session-day's trade cards, each with a downsampled underlying
    sparkline over its hold window. Reuses `list_trades` filter semantics
    (excludes context='training', includes sim). Empty day -> 200 with []."""
    trades = list_trades(from_=day, to=day)
    # list_trades orders open_ts DESC; the day scan wants chronological ASC.
    trades = sorted(trades, key=lambda t: t.open_ts)

    # Cache candles per symbol so a day with many trades on one ticker only
    # hits the provider once.
    candle_cache: dict[str, list] = {}
    cards: list[DayTradeCard] = []
    for t in trades:
        if t.symbol not in candle_cache:
            try:
                candle_cache[t.symbol] = provider.get_candles(t.symbol, day, "1m")
            except WebullError:
                # Sparkline is decorative; a symbol the provider can't fetch
                # (e.g. an index-option root like SPXW as US_STOCK -> 417) must
                # not take down the whole day page. Degrade to no sparkline.
                candle_cache[t.symbol] = []
        sparkline = downsample_closes(
            candle_cache[t.symbol], t.open_ts, t.close_ts
        )
        cards.append(
            DayTradeCard(
                id=t.id,
                symbol=t.symbol,
                contract_label=t.contract_label,
                direction=t.direction,
                net_pnl=t.net_pnl,
                pnl_pct=t.pnl_pct,
                r_multiple=t.r_multiple,
                hold_time_s=t.hold_time_s,
                grade=t.enrichment.grade if t.enrichment else None,
                open_ts=t.open_ts,
                close_ts=t.close_ts,
                simulated=t.simulated,
                sparkline=sparkline,
            )
        )

    return DayTradesResponse(date=day.isoformat(), trades=cards)
