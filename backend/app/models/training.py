"""Training-module (M6) domain models — FROZEN CONTRACT v1.

See docs/PLAN-M6-TRAINING.md §3 (schema) and §4 (API surface).

Two invariants every implementer must preserve:

1. **Answer-key hiding**: `answer_key` / `explanation` appear ONLY in
   `AttemptFinishResponse` (returned by POST /training/attempts/{id}/finish).
   No scenario GET/list response, and no pre-finish attempt response, may
   include them. This is the no-cheating equivalent of the no-lookahead
   guarantee — qa tests enforce it (task 6.0.5).

2. **Replayable attempts**: user actions are stored as a bar-indexed event
   log (`AttemptAction`), never collapsed to an outcome. Later phases score
   *behavior* (moved stops, revenge sizing) from this log.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.market import Candle


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class ScenarioSource(str, Enum):
    real = "real"            # references candles_cache (symbol + session_date)
    synthetic = "synthetic"  # regenerated deterministically from synth_seed
    debrief = "debrief"      # authored by the debrief agent from a real/sim trade


class Archetype(str, Enum):
    gap_and_go = "gap-and-go"
    failed_breakout = "failed-breakout"
    chop = "chop"
    trend_day = "trend-day"
    custom = "custom"


class DrillType(str, Enum):
    decision_point = "decision-point"
    management_only = "management-only"
    do_nothing = "do-nothing"
    spot_setup = "spot-setup"
    drawdown_script = "drawdown-script"


class AttemptActionType(str, Enum):
    enter_long = "enter_long"
    enter_short = "enter_short"
    exit = "exit"
    skip = "skip"          # explicit "no trade" decision at the decision bar
    wait = "wait"          # defer the decision one bar
    set_stop = "set_stop"
    move_stop = "move_stop"


class MoodCheckin(str, Enum):
    calm = "calm"
    rushed = "rushed"
    frustrated = "frustrated"


class DebriefVerdict(str, Enum):
    setup_failed = "setup-failed"        # good trade that lost — setup didn't work
    failed_the_setup = "failed-the-setup"  # valid setup, execution/psychology broke it
    good_loss = "good-loss"              # followed plan, took planned risk, lost
    bad_win = "bad-win"                  # broke rules and got lucky
    clean = "clean"                      # followed plan and won


class TrainingContext(str, Enum):
    """Values for trades.context — journal analytics exclude 'training'."""

    real = "real"
    sim = "sim"
    training = "training"


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

class ScenarioAnnotation(BaseModel):
    """A bar-pinned teaching note. `bar` is an index into the sliced range."""

    bar: int
    text: str
    kind: str = "note"  # note | confluence | warning


class AnswerKey(BaseModel):
    """Correct action(s) + explanation. NEVER serialized before finish."""

    correct_actions: list[AttemptActionType]
    explanation: str
    # Optional per-bar management guidance for management-only drills:
    # [{"bar": int, "action": str, "note": str}]
    management_notes: list[dict] = Field(default_factory=list)


class ScenarioCreate(BaseModel):
    """POST /api/training/scenarios — authoring UI and debrief agent both use this."""

    title: str
    archetype: Archetype = Archetype.custom
    source_type: ScenarioSource
    drill_type: DrillType = DrillType.decision_point

    # real | debrief source: reference into candles_cache
    symbol: str | None = None
    session_date: date | None = None

    # synthetic source: deterministic regeneration parameters
    synth_seed: int | None = None
    synth_params: dict | None = None

    start_bar: int = 0
    decision_bar: int | None = None  # bar (relative to start_bar) where drill pauses
    end_bar: int | None = None

    annotations: list[ScenarioAnnotation] = Field(default_factory=list)
    answer_key: AnswerKey
    checklist_template_id: int | None = None
    source_trade_id: int | None = None
    blind_default: bool = False


class ScenarioPublic(BaseModel):
    """Scenario as returned by GET endpoints. NO answer_key, by contract."""

    id: int
    title: str
    archetype: Archetype
    source_type: ScenarioSource
    drill_type: DrillType
    symbol: str | None = None          # None when blind
    session_date: date | None = None   # None when blind
    start_bar: int
    decision_bar: int | None = None
    end_bar: int | None = None
    annotations: list[ScenarioAnnotation] = Field(default_factory=list)
    checklist_template_id: int | None = None
    blind_default: bool = False
    active: bool = True
    created_at: datetime


class ScenarioSessionResponse(BaseModel):
    """GET /api/training/scenarios/{id}/session — the drill replay payload.

    Same causal alignment contract as ReplaySessionResponse (models/replay.py):
    every indicator/level array is aligned index-for-index with `candles`, and
    the value at i uses only bars <= i **of the full underlying session** —
    slicing must not break causality (quant task 6.0.4).

    When `blind` is true, symbol and session_date are omitted and timestamps
    are normalized so the ticker/date cannot be inferred.
    """

    scenario_id: int
    drill_type: DrillType
    blind: bool
    symbol: str | None = None
    session_date: date | None = None
    decision_bar: int | None = None  # index into `candles` where the UI pauses
    candles: list[Candle]
    indicators: dict[str, list[float | None]] = Field(default_factory=dict)
    levels: dict[str, list[float | None]] = Field(default_factory=dict)
    annotations: list[ScenarioAnnotation] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Attempts
# --------------------------------------------------------------------------- #

class AttemptAction(BaseModel):
    """One user decision, bar-indexed. The event log is append-only."""

    bar: int
    action: AttemptActionType
    price: float | None = None
    qty: float | None = None
    ts: datetime  # wall-clock UTC when the user acted (hesitation metric)


class AttemptCreate(BaseModel):
    scenario_id: int
    mood_checkin: MoodCheckin | None = None
    blind: bool | None = None  # override scenario.blind_default


class AttemptEventsPatch(BaseModel):
    """PATCH /api/training/attempts/{id} — append new actions (never rewrite)."""

    actions: list[AttemptAction]


class Attempt(BaseModel):
    """Attempt as returned pre-finish. No score, no answer key."""

    id: int
    scenario_id: int
    started_at: datetime
    finished_at: datetime | None = None
    mood_checkin: MoodCheckin | None = None
    actions: list[AttemptAction] = Field(default_factory=list)


class AttemptFinishResponse(BaseModel):
    """POST /api/training/attempts/{id}/finish — the ONLY place the answer
    key and process score are ever exposed. Scoring is server-authoritative
    (quant, app/analytics/training.py)."""

    attempt: Attempt
    process_score: float  # 0..1 — rule adherence + decision correctness
    outcome_pnl: float | None = None  # recorded, displayed, NEVER rewarded
    checklist: list[dict] = Field(default_factory=list)
    answer_key: AnswerKey
    verdict: str | None = None


# --------------------------------------------------------------------------- #
# Debriefs
# --------------------------------------------------------------------------- #

class DebriefStep(str, Enum):
    setup = "setup"        # what did you see? confluence?
    plan = "plan"          # stop / target / R; agent cross-checks fills
    feelings = "feelings"  # emotional state, timestamped
    verdict = "verdict"    # setup failed vs failed the setup
    done = "done"


class DebriefCreate(BaseModel):
    trade_id: int


class DebriefReply(BaseModel):
    """POST /api/debriefs/{id}/turn — the user's answer to the last question."""

    text: str
    # form-backend structured answers ride alongside free text when present
    fields: dict | None = None


class InterviewTurn(BaseModel):
    """One agent turn. Emitted by every LLM backend (form/cli/api) — identical
    shape so the frontend renders chat and stepper from the same contract."""

    step: DebriefStep
    question: str | None = None            # None when step == done
    options: list[str] | None = None       # form backend: suggested choices
    context_note: str | None = None        # e.g. drift flag: "stop stated 12.40, exited 12.31"
    done: bool = False


class DebriefArtifacts(BaseModel):
    """What a completed debrief produced. Returned by /finish."""

    enrichment_saved: bool = False
    verdict: DebriefVerdict | None = None
    scenario_id: int | None = None  # emitted drill, if the user opted in


class Debrief(BaseModel):
    id: int
    trade_id: int
    llm_backend: str  # none | claude-cli | api
    step: DebriefStep
    transcript: list[dict] = Field(default_factory=list)  # [{"role","step","text"}]
    plan: dict | None = None       # stated stop/target/R + drift flags
    feelings: list[dict] = Field(default_factory=list)
    verdict: DebriefVerdict | None = None
    scenario_id: int | None = None
    created_at: datetime


class DebriefFinish(BaseModel):
    """POST /api/debriefs/{id}/finish."""

    emit_scenario: bool = False
