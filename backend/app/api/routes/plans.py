"""Plan card routes (issue #9): POST /api/plans, GET /api/plans?date=YYYY-MM-DD."""
from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, HTTPException

from app.desk import plans as store
from app.models.plans import Plan, PlanCreate

router = APIRouter(tags=["plans"])


@router.post("/plans", response_model=Plan)
def create_plan(body: PlanCreate) -> Plan:
    try:
        return store.add_plan(
            caller=body.caller, contract=body.contract, size_usd=body.size_usd,
            max_loss_usd=body.max_loss_usd, target=body.target, note=body.note,
            session_date=body.session_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/plans", response_model=list[Plan])
def get_plans(date: date_type | None = None) -> list[Plan]:
    return store.list_plans(date or store.today_pt())
