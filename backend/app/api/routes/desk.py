"""Desk routes: GET /api/rules, /api/days, /api/days/{date}, /api/patterns, /api/line;
GET/PUT /api/days/{date}/fields (the day journal)."""
from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.desk import day_fields as desk_day_fields
from app.desk import days as desk_days
from app.desk import serialize
from app.config import get_settings
from app.ingest.excursion_rollup import attach_excursions
from app.desk.rules import TZ_NAME, Rules, load_rules
from app.models.desk import (
    DayDetail,
    DayFields,
    DaysResponse,
    LineResponse,
    PatternsResponse,
)

router = APIRouter(tags=["desk"])


def _excursions(days, rules: Rules) -> str:
    """Attach excursion facts (#52); returns the bars mode. Mock settings use the
    deterministic mock provider (nothing cached); otherwise cached bars only, so these
    read routes never call a provider."""
    settings = get_settings()
    if settings.mode == "mock":
        from app.providers.mock import MockProvider

        attach_excursions(days, rules, MockProvider(settings), None, "mock")
        return "mock"
    attach_excursions(days, rules, None, None, "cache")
    return "cache"


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
    mode = _excursions([d], rules)
    return serialize.day_detail(day, d, rules, desk_days.as_of(), mode)


class DayFieldsPatch(BaseModel):
    """PUT body: any subset of the day_fields whitelist. Omitted fields are left alone;
    an explicit null clears a nullable field. Unknown fields are rejected (422)."""

    model_config = ConfigDict(extra="forbid")

    why: str | None = None
    callers: list[str] | None = None
    size_written_first: bool | None = None
    max_loss_declared: float | None = None
    closed_by_window_end: bool | None = None
    stop_hit: bool | None = None
    target_hit: bool | None = None
    how_felt: str | None = None
    notes: str | None = None


@router.get("/days/{day}/fields", response_model=DayFields | None)
def get_day_fields(day: date_type) -> DayFields | None:
    """The day journal (self-report) for one date; null when nothing was written yet."""
    return desk_day_fields.get_day_fields(day)


@router.put("/days/{day}/fields", response_model=DayFields)
def put_day_fields(day: date_type, body: DayFieldsPatch) -> DayFields:
    """Partial update of the day journal: same store and validation as the MCP tool."""
    fields = {name: getattr(body, name) for name in body.model_fields_set}
    if not fields:
        raise HTTPException(status_code=422, detail="no day fields given")
    try:
        return desk_day_fields.upsert_day_fields(day, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/patterns", response_model=PatternsResponse)
def get_patterns(
    from_: date_type | None = Query(None, alias="from"),
    to: date_type | None = Query(None),
) -> PatternsResponse:
    rules, a, days = desk_days.patterns(from_, to, _rules())
    mode = _excursions(days, rules)
    return serialize.patterns_response(rules, a, days, from_, to, desk_days.as_of(), mode)


@router.get("/line", response_model=LineResponse)
def get_line() -> LineResponse:
    ls = desk_days.line_status(rules=_rules())
    return serialize.line_response(ls, desk_days.as_of())
