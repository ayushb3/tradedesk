"""Desk routes. GET /api/rules returns the loaded rules config."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.desk.rules import TZ_NAME, load_rules

router = APIRouter(tags=["desk"])


@router.get("/rules")
def get_rules() -> dict:
    try:
        rules = load_rules()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {**rules.model_dump(mode="json"), "tz": TZ_NAME}
