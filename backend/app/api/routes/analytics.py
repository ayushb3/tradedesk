"""Journal analytics (M3).

Contract seam (quant owns app/analytics/):
    stats.summary(**filters) -> dict          # win_rate, profit_factor, avg_r, expectancy, hold_time, counts
    stats.calendar(month: str) -> dict         # {"YYYY-MM-DD": {"pnl", "trades", "wins", "losses"}}
    stats.by_dimension(dimension, **filters) -> list[dict]   # dimension in {ticker,tod,tag,setup}
    stats.daily_review(day: date) -> dict      # {"trades":[...], "prompts":[...], "summary":{...}}
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["analytics"])


def _stats():
    from app.analytics import stats  # lazy

    return stats


@router.get("/analytics/summary")
def summary(
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    symbol: str | None = None,
    simulated: bool | None = None,
) -> dict:
    try:
        return _stats().summary(from_=from_, to=to, symbol=symbol, simulated=simulated)
    except (ImportError, NotImplementedError):
        raise HTTPException(status_code=501, detail="analytics not implemented")


@router.get("/analytics/calendar")
def calendar(month: str = Query(..., description="YYYY-MM")) -> dict:
    try:
        return _stats().calendar(month)
    except (ImportError, NotImplementedError):
        raise HTTPException(status_code=501, detail="analytics not implemented")


@router.get("/analytics/by")
def by_dimension(
    dimension: str = Query(..., pattern="^(ticker|tod|tag|setup)$"),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
) -> list[dict]:
    try:
        return _stats().by_dimension(dimension, from_=from_, to=to)
    except (ImportError, NotImplementedError):
        raise HTTPException(status_code=501, detail="analytics not implemented")


@router.get("/daily-review")
def daily_review(date_: date = Query(..., alias="date")) -> dict:
    try:
        return _stats().daily_review(date_)
    except (ImportError, NotImplementedError):
        raise HTTPException(status_code=501, detail="analytics not implemented")
