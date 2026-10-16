"""Coach-agent endpoints (M9a) — Trade Critic + shared agent infrastructure.

    POST /api/agents/critic              -> critique a trade against course rules (R6–R11)
    GET  /api/agents/status              -> LLM availability (gates the UI Critique button)
    GET  /api/agents/sessions/{id}       -> a persisted agent session + its messages (R4)
    GET  /api/agents/sessions?subject_ref=&kind=  -> list sessions (newest first)

Read-only and degrade-clean (R1): under `TRADEDESK_LLM=none` (or any LLM
failure) `POST /agents/critic` returns `available=False, status="llm_unavailable"`
and the frontend hides the Critique affordances via `GET /agents/status`. No
endpoint 500s on an unavailable LLM. No order-entry code, ever.

Contract seams (this file is PM-owned; implementers own the modules below and
code against these exact signatures — mirrors routes/debriefs.py / routes/training.py):

data-engineer — `app.db.agents_store` (M9a):
    create_session(kind: str, subject_ref: str) -> dict            # {id, kind, subject_ref, started_at}
    append_message(session_id: int, role: str, content_md: str, *,
                   citations: list[dict] | None = None,
                   payload: dict | None = None) -> dict             # the persisted message row
    get_session(session_id: int) -> dict | None                     # {..., messages: [...]}; None if unknown
    list_sessions(subject_ref: str | None = None,
                  kind: str | None = None) -> list[dict]            # summaries, newest first
    get_latest_session_for_subject(subject_ref: str,
                  kind: str | None = None) -> dict | None

backend dev / dev1 — `app.agents.critic` + `app.llm.context` (M9a):
    build_knowledge_context(trade, tags, detected_patterns, *, k=8) -> KnowledgeContext   # app.llm.context
    critique_trade(inputs, *, settings=None, run_fn=None) -> CriticResult | None  # app.agents.critic

reused verbatim — `app.llm.context.build_debrief_context` (M6): assembles
    trade + fills + candles_around + enrichment + tags (R7's non-curriculum half).
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import provider_dep, settings_dep
from app.config import Settings
from app.models.agents import (
    AgentSessionResponse,
    AgentSessionsResponse,
    AgentStatusResponse,
    CriticResponse,
)
from app.models.training import DebriefCreate
from app.providers.base import DataProvider

router = APIRouter(prefix="/agents", tags=["agents"])

_LLM_UNAVAILABLE = (
    "Coaching agents require an LLM backend. Set TRADEDESK_LLM=claude-cli "
    "(or =api). Under TRADEDESK_LLM=none the critic is unavailable by design."
)


@router.get("/status", response_model=AgentStatusResponse)
def agents_status(settings: Settings = Depends(settings_dep)) -> AgentStatusResponse:
    """Whether coaching agents can run. `available` is false under
    `TRADEDESK_LLM=none` so the UI hides the Critique affordances without probing
    an endpoint. Never 500s."""
    return AgentStatusResponse(available=settings.llm != "none", llm=settings.llm)


@router.post("/critic", response_model=CriticResponse)
def run_critic(
    payload: DebriefCreate,
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> CriticResponse:
    """Critique *payload.trade_id* against course rules (R6–R11). Mirrors
    start_debrief: reuse build_debrief_context for the trade half, build the knowledge
    context, run the critic, persist the session, and return the structured
    result. 404 when the trade is unknown; `available=False` (never 500) when the
    LLM is unavailable or produced nothing.

    Wired by the PM to app.agents.critic / app.db.agents_store once dev1 +
    data-engineer land their modules (this stub keeps the endpoint importable and
    degrade-clean meanwhile).
    """
    if settings.llm == "none":
        return CriticResponse(
            available=False,
            status="llm_unavailable",
            detail=_LLM_UNAVAILABLE,
            llm_backend=settings.llm,
        )

    try:
        from app.agents.critic import CriticInputs, critique_trade
        from app.db.agents_store import append_message, create_session
        from app.llm.context import build_debrief_context, build_knowledge_context
    except ImportError:
        # Orchestrator/store not implemented yet — degrade clean, never 500.
        return CriticResponse(
            available=False,
            status="llm_unavailable",
            detail="Critic orchestrator not implemented yet (M9a, dev1/data-eng).",
            llm_backend=settings.llm,
        )

    # Reuse the M6 debrief context assembler verbatim for the trade half (R7):
    # trade + fills + candles_around + enrichment + tags. An unknown trade is a
    # hard client error (404); everything else degrades to available=False so
    # R1 holds — the endpoint never 500s on an infra/LLM/store failure.
    try:
        ctx = build_debrief_context(provider, payload.trade_id, settings)
    except ValueError:
        raise HTTPException(status_code=404, detail="trade not found")

    try:
        # Latest debrief for this trade, if one exists (optional context, R7).
        debrief = _latest_debrief(payload.trade_id)

        tags = ctx.tags
        # build_knowledge_context degrades to available=False on "not ingested" etc.,
        # but the underlying embedder/vector index can still RAISE at call time
        # (model load failure, corrupt chroma dir) — caught here, not surfaced.
        knowledge = build_knowledge_context(
            ctx.trade, tags, detected_patterns=[t.name for t in tags]
        )
        result = critique_trade(
            CriticInputs(trade_ctx=ctx, knowledge=knowledge, debrief=debrief),
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 — R1: degrade, never 500
        print(f"[critic] context/critique failed: {exc!r}", file=sys.stderr)
        return CriticResponse(
            available=False,
            status="llm_unavailable",
            detail=_LLM_UNAVAILABLE,
            llm_backend=settings.llm,
        )

    if result is None:
        return CriticResponse(
            available=False,
            status="llm_unavailable",
            detail=_LLM_UNAVAILABLE,
            llm_backend=settings.llm,
        )

    # Persist the session + message. A store failure must not turn a
    # successfully-computed critique into a 500 — return the result and note the
    # session could not be persisted (session_id=None) rather than erroring.
    try:
        session = create_session("critic", f"trade:{payload.trade_id}")
        citations = [
            rc.source_citation.model_dump()
            for rc in result.rule_checks
            if rc.source_citation is not None
        ]
        append_message(
            session["id"],
            "agent",
            _verdicts_to_markdown(result),
            citations=citations,
            payload=result.model_dump(),
        )
        session_id = session["id"]
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        print(f"[critic] session persist failed: {exc!r}", file=sys.stderr)
        session_id = None

    return CriticResponse(
        available=True,
        status="ok",
        session_id=session_id,
        llm_backend=settings.llm,
        result=result,
    )


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse)
def get_agent_session(session_id: int) -> AgentSessionResponse:
    """One persisted agent session + its messages (R4). 404 when unknown."""
    try:
        from app.db.agents_store import get_session
    except ImportError:
        raise HTTPException(status_code=404, detail="agent sessions not available")
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return AgentSessionResponse(**session)


@router.get("/sessions", response_model=AgentSessionsResponse)
def list_agent_sessions(
    subject_ref: str | None = Query(None),
    kind: str | None = Query(None),
) -> AgentSessionsResponse:
    """Sessions matching the filters, newest first (R4)."""
    try:
        from app.db.agents_store import list_sessions
    except ImportError:
        return AgentSessionsResponse(sessions=[])
    return AgentSessionsResponse(sessions=list_sessions(subject_ref=subject_ref, kind=kind))


# --------------------------------------------------------------------------- #
# Local helpers — kept thin; the real critic logic lives in dev1's app.agents
# package and the store in data-eng's app.db.agents_store.
# --------------------------------------------------------------------------- #


def _latest_debrief(trade_id: int) -> dict | None:
    """The most recent debrief for a trade as a plan/feelings/verdict dict, or
    None. Optional critic context (R7) — never blocks the critique if the
    training store is unavailable."""
    try:
        from app.db.training_store import list_debriefs
    except ImportError:
        return None
    try:
        debriefs = list_debriefs(trade_id=trade_id)
    except Exception:
        return None
    if not debriefs:
        return None
    latest = debriefs[-1]
    return {
        "plan": getattr(latest, "plan", None),
        "feelings": getattr(latest, "feelings", None),
        "verdict": getattr(latest, "verdict", None),
    }


def _verdicts_to_markdown(result) -> str:
    """Flatten the phase verdicts into a single markdown blob for the persisted
    message body (the structured result rides in payload_json)."""
    v = result.verdict_by_phase
    return (
        f"**Setup** — {v.setup}\n\n"
        f"**Entry** — {v.entry}\n\n"
        f"**Management** — {v.management}\n\n"
        f"**Exit** — {v.exit}"
    )
