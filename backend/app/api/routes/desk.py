"""Desk routes: GET /api/rules, /api/days, /api/days/{date}, /api/patterns, /api/line."""
from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Query

from app.desk import days as desk_days
from app.desk import serialize
from app.desk.rules import TZ_NAME, Rules, load_rules
from app.models.desk import DayDetail, DaysResponse, LineResponse, PatternsResponse

router = APIRouter(tags=["desk"])


def _rules() -> Rules:
    try:
        return load_rules()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rules")
def get_rules() -> dict:
    rules = _rules()
    return {**rules.model_dump(mode="json"), "tz": TZ_NAME}


@router.get("/days", response_model=DaysResponse)
def get_days() -> DaysResponse:
    rules, days = desk_days.list_days(_rules())
    return serialize.days_response(rules, days, desk_days.as_of())


@router.get("/days/{day}", response_model=DayDetail)
def get_day(day: date_type) -> DayDetail:
    rules = _rules()
    d = desk_days.get_day(day, rules)
    if d is None:
        raise HTTPException(status_code=404, detail=f"no trades or day fields for {day}")
    return serialize.day_detail(day, d, rules, desk_days.as_of())


@router.get("/patterns", response_model=PatternsResponse)
def get_patterns(
    from_: date_type | None = Query(None, alias="from"),
    to: date_type | None = Query(None),
) -> PatternsResponse:
    rules, a, days = desk_days.patterns(from_, to, _rules())
    return serialize.patterns_response(rules, a, days, from_, to, desk_days.as_of())


@router.get("/line", response_model=LineResponse)
def get_line() -> LineResponse:
    ls = desk_days.line_status(rules=_rules())
    return serialize.line_response(ls, desk_days.as_of())
