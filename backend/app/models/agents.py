"""Coach-agent API schemas (M9a) — FROZEN CONTRACT v1.

Router-owned models for the Trade Critic (R6–R11) and the shared agent
infrastructure (R1–R5). Kept separate from the orchestration internals in
`app.agents` so the HTTP contract is stable while the critic pipeline evolves.

Invariants every implementer must preserve:

1. **No P&L judgment** (R11 / anti-gambling). There is intentionally NO dollar /
   pnl / win-loss field anywhere in `CriticResult` or `CriticRuleCheck`. The
   critic scores PROCESS ADHERENCE to course rules only — a rule-following loser
   critiques better than a rule-breaking winner. Adding a P&L field here is a
   contract violation, not a feature.

2. **Every curriculum claim carries a citation** (R3). Each `rule_check` has an
   optional `source_citation` (a `KnowledgeSearchHit` — citation-complete). When the
   cited lesson is not in the assembled course context, or the citation fails the
   faithfulness check, the orchestrator DROPS the citation to None and the UI
   renders the check as visually distinct "unverified". Claims are never
   fabricated; weak ones are demoted, not dropped silently.

3. **Degrade to nothing** (R1/R2). Every response model carries `available` +
   `status`; under `TRADEDESK_LLM=none` (or any failure) the endpoints return
   `available=False` and the UI affordances gate off. Endpoints never 500 on an
   unavailable LLM.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.knowledge import KnowledgeSearchHit
from app.models.training import ScenarioCreate
from app.models.utc import UtcDatetime


# --------------------------------------------------------------------------- #
# Shared agent infrastructure (R2/R4)
# --------------------------------------------------------------------------- #


class KnowledgeContext(BaseModel):
    """The curriculum half of the critic's context (R2/R7).

    Assembled deterministically by `app.llm.context.build_knowledge_context` from the
    trade's setup-tag names + detected patterns + ET time-of-day bucket — one
    `retrieve()` call, no tool loop. Degrades to `available=False` with an empty
    `hits` list when the KB is not ingested or embeddings are unavailable; the
    critic then reports `unclear` rather than fabricating citations.
    """

    available: bool
    status: str                # "ok" | "not_ingested" | "index_empty" | …
    query: str                 # the deterministic query that was retrieved on
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)
    # Concept slugs expanded from the retrieved lessons (dedup, max-weight, desc).
    concepts: list[str] = Field(default_factory=list)


# Adherence is a first-class three-valued judgment: `unclear` is REWARDED for
# abstention (PRD §7 risk #2). It is never inferred from P&L.
Adherence = Literal["pass", "fail", "unclear"]

# Which phase of the trade a rule-check pins to.
TradePhase = Literal["setup", "entry", "management", "exit"]


class CriticRuleCheck(BaseModel):
    """One falsifiable rule-check against a course rule.

    `source_citation` is the citation-complete `KnowledgeSearchHit` for the lesson the
    rule comes from — `None` when the check is *unverified* (the model cited a
    lesson not in context, or the citation failed the string-overlap
    faithfulness heuristic). The UI renders `source_citation is None` checks as
    visually distinct "unverified" (R3).

    `evidence_bar_ref` is an ISO-8601 UTC timestamp pinning the check to a bar so
    the M7 ReviewChart can annotate a pass/fail/unclear marker at the exact bar
    (R9). `None` when the check does not pin to a specific bar.
    """

    rule: str
    adherence: Adherence
    source_citation: KnowledgeSearchHit | None = None
    evidence_bar_ref: str | None = None  # ISO-8601 UTC ts of the pinned bar
    note: str | None = None              # short falsifiable rationale (no P&L)


class CriticPhaseVerdicts(BaseModel):
    """Per-phase prose verdicts (R8). Phrased as rule-checks, never P&L
    narratives. Each is a short markdown string (M8 reader convention: plain
    markdown + optional Mermaid, no markdown lib)."""

    setup: str
    entry: str
    management: str
    exit: str


class CriticPrescription(BaseModel):
    """One drill + readings (R10).

    Exactly one of `drill_id` / `drill_spec` is set. `drill_id` references an
    existing M6 scenario (validated against `training_store.get_scenario`);
    `drill_spec` is a `ScenarioCreate` the M6 authoring path (`POST
    /api/training/scenarios`) consumes directly to create the drill. `readings`
    are KB lesson ids that deep-link into the M8 reader (filtered to real
    lessons by the orchestrator)."""

    drill_id: int | None = None
    drill_spec: ScenarioCreate | None = None
    readings: list[int] = Field(default_factory=list)  # course lesson ids


class CriticResult(BaseModel):
    """The structured critic output (R8 verbatim). No P&L field, by design."""

    verdict_by_phase: CriticPhaseVerdicts
    rule_checks: list[CriticRuleCheck] = Field(default_factory=list)
    prescription: CriticPrescription = Field(default_factory=CriticPrescription)


class CriticResponse(BaseModel):
    """`POST /api/agents/critic` envelope. Degrades cleanly (R1): under
    `TRADEDESK_LLM=none` or any failure returns `available=False`,
    `status="llm_unavailable"`, `result=None` — never a 500."""

    available: bool
    status: str                # "ok" | "llm_unavailable" | "knowledge_unavailable"
    detail: str | None = None
    session_id: int | None = None
    llm_backend: str | None = None  # "none" | "claude-cli" | "api"
    result: CriticResult | None = None


class AgentStatusResponse(BaseModel):
    """`GET /api/agents/status` — gates the frontend Critique affordances.
    `available` is false under `TRADEDESK_LLM=none` so the UI hides the buttons
    without probing an endpoint that would 500."""

    available: bool
    llm: str                   # the configured backend: "none" | "claude-cli" | "api"


# --------------------------------------------------------------------------- #
# Persisted sessions (R4) — reviewable later, joinable to trades
# --------------------------------------------------------------------------- #


class AgentMessageResponse(BaseModel):
    """One persisted agent/user message. `payload_json` carries the structured
    `CriticResult` for agent messages that produced one (additive/nullable)."""

    id: int
    session_id: int
    role: str                  # "agent" | "user"
    content_md: str
    citations: list[KnowledgeSearchHit] = Field(default_factory=list)
    payload: CriticResult | None = None
    ts: UtcDatetime


class AgentSessionResponse(BaseModel):
    """A persisted agent session with its messages (R4). `subject_ref` joins to
    a trade (e.g. ``"trade:42"``); `kind` is the agent that ran (e.g.
    ``"critic"``)."""

    id: int
    kind: str                  # "critic" (M9b/M9c add "tutor" | "mapper")
    subject_ref: str           # e.g. "trade:42"
    started_at: UtcDatetime
    messages: list[AgentMessageResponse] = Field(default_factory=list)


class AgentSessionSummary(BaseModel):
    """List-view row for `GET /api/agents/sessions` (no message bodies)."""

    id: int
    kind: str
    subject_ref: str
    started_at: UtcDatetime
    message_count: int


class AgentSessionsResponse(BaseModel):
    """`GET /api/agents/sessions?subject_ref=&kind=` — sessions matching the
    filters, newest first."""

    sessions: list[AgentSessionSummary] = Field(default_factory=list)
