"""Debrief-interview endpoints (M6) — agent-guided trade self-analysis.

API surface per docs/PLAN-M6-TRAINING.md §1/§4. Fixed four-step arc
regardless of LLM backend: setup → plan → feelings → verdict. The brain is
the `app.llm.LLMProvider` seam (form / claude-cli / api), selected by
`TRADEDESK_LLM` — every backend emits the same `InterviewTurn` shape.

Contract seams (this file is PM-owned; implementers own the modules below and
code against these exact signatures):

data-engineer — `app.db.training_store` (task 6.0.2):
    create_debrief(payload: DebriefCreate, llm_backend: str) -> Debrief | None
        # None if payload.trade_id is unknown (route maps to 404)
    get_debrief(debrief_id: int) -> Debrief | None
    append_debrief_transcript(debrief_id: int, entries: list[dict]) -> Debrief | None
        # entries: [{"role": "agent"|"user", "step": str, "text": str}]
        # append-only; returns the updated Debrief, None if unknown
    list_debriefs(trade_id: int | None = None) -> list[Debrief]
    finish_debrief(debrief_id: int, finish: DebriefFinish) -> DebriefArtifacts | None
        # persists plan/feelings/verdict, writes enrichment to the trade,
        # and (when finish.emit_scenario) creates the drill scenario via
        # create_scenario. None if debrief unknown.

data-engineer — `app.llm` (task 6.0.3 lands the default form backend):
    get_llm_provider(settings) -> LLMProvider           # app/llm/base.py, PM-frozen
    build_debrief_context(provider, trade_id: int, settings) -> DebriefContext
        # lives in app.llm.context — assembles trade + fills + candles_around
        # + enrichment + tags + checklist template BEFORE the first question,
        # so backends ask specific questions, never generic ones.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import provider_dep, settings_dep
from app.config import Settings
from app.llm import get_llm_provider
from app.models.training import (
    Debrief,
    DebriefArtifacts,
    DebriefCreate,
    DebriefFinish,
    DebriefReply,
    InterviewTurn,
)
from app.providers.base import DataProvider

router = APIRouter(tags=["debriefs"])

_STORE_501 = "debrief store not implemented (data-eng task 6.0.2)"
_CONTEXT_501 = "debrief context builder not implemented (data-eng task 6.0.3)"


class DebriefStartResponse(BaseModel):
    """POST /api/debriefs response envelope: the created debrief (the client
    keeps its id for /turn and /finish) plus the first agent turn.
    Route-local envelope by design — not a domain model."""

    debrief: Debrief
    turn: InterviewTurn


@router.post("/debriefs", response_model=DebriefStartResponse)
def start_debrief(
    payload: DebriefCreate,
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> DebriefStartResponse:
    try:
        from app.db.training_store import append_debrief_transcript, create_debrief
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        from app.llm.context import build_debrief_context
    except ImportError:
        raise HTTPException(status_code=501, detail=_CONTEXT_501)
    try:
        llm = get_llm_provider(settings)
        ctx = build_debrief_context(provider, payload.trade_id, settings)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    debrief = create_debrief(payload, llm_backend=llm.name)
    if debrief is None:
        raise HTTPException(status_code=404, detail="trade not found")
    turn = llm.start_interview(ctx)
    updated = append_debrief_transcript(
        debrief.id,
        [{"role": "agent", "step": turn.step.value, "text": turn.question or ""}],
    )
    return DebriefStartResponse(debrief=updated or debrief, turn=turn)


@router.post("/debriefs/{debrief_id}/turn", response_model=InterviewTurn)
def debrief_turn(
    debrief_id: int,
    reply: DebriefReply,
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> InterviewTurn:
    try:
        from app.db.training_store import append_debrief_transcript, get_debrief
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        from app.llm.context import build_debrief_context
    except ImportError:
        raise HTTPException(status_code=501, detail=_CONTEXT_501)
    debrief = get_debrief(debrief_id)
    if debrief is None:
        raise HTTPException(status_code=404, detail="debrief not found")
    try:
        llm = get_llm_provider(settings)
        ctx = build_debrief_context(provider, debrief.trade_id, settings)
        turn = llm.next_turn(ctx, debrief.transcript, reply)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    append_debrief_transcript(
        debrief_id,
        [
            {"role": "user", "step": debrief.step.value, "text": reply.text},
            {"role": "agent", "step": turn.step.value, "text": turn.question or ""},
        ],
    )
    return turn


@router.post("/debriefs/{debrief_id}/finish", response_model=DebriefArtifacts)
def finish_debrief(debrief_id: int, payload: DebriefFinish) -> DebriefArtifacts:
    try:
        from app.db.training_store import finish_debrief as _finish
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        artifacts = _finish(debrief_id, payload)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="debrief not found")
    return artifacts


@router.get("/debriefs", response_model=list[Debrief])
def list_debriefs(trade_id: int | None = None) -> list[Debrief]:
    try:
        from app.db.training_store import list_debriefs as _list
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    return _list(trade_id=trade_id)
