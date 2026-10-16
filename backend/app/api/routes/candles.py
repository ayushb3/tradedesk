from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import provider_dep
from app.models.market import Candle
from app.providers.base import DataProvider

router = APIRouter(tags=["candles"])


@router.get("/candles", response_model=list[Candle])
def get_candles(
    symbol: str = Query(...),
    date_: date = Query(..., alias="date"),
    timeframe: str = "1m",
    provider: DataProvider = Depends(provider_dep),
) -> list[Candle]:
    try:
        return provider.get_candles(symbol.upper(), date_, timeframe)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="provider not implemented")


@router.get("/candles/range", response_model=list[Candle])
def get_candles_range(
    symbol: str = Query(...),
    start: datetime = Query(...),
    end: datetime = Query(...),
    timeframe: str = "1m",
    provider: DataProvider = Depends(provider_dep),
) -> list[Candle]:
    try:
        return provider.get_candles_range(symbol.upper(), start, end, timeframe)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="provider not implemented")
