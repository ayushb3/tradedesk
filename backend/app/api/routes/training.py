"""Training-module endpoints (M6) — scenarios + drill attempts.

API surface per docs/PLAN-M6-TRAINING.md §4. Two invariants (models/training.py):

1. Answer-key hiding: `answer_key` / `explanation` appear ONLY in
   `AttemptFinishResponse` (POST /training/attempts/{id}/finish). No scenario
   GET/list response and no pre-finish attempt response may include them.
2. Replayable attempts: PATCH appends bar-indexed `AttemptAction` events —
   append-only, never rewrites.

Contract seams (this file is PM-owned; implementers own the modules below and
code against these exact signatures):

data-engineer — `app.db.training_store` (task 6.0.2):
    create_scenario(payload: ScenarioCreate) -> ScenarioPublic
    list_scenarios(archetype: Archetype | None = None,
                   drill_type: DrillType | None = None,
                   active: bool | None = None) -> list[ScenarioPublic]
    get_scenario(scenario_id: int) -> ScenarioPublic | None
        # Public shape: must blank symbol/session_date when the scenario's
        # blind_default is true (blind-mode leak is qa-tested, task 6.0.5).
    get_scenario_full(scenario_id: int) -> ScenarioFull | None
        # ScenarioFull is defined by data-eng IN training_store:
        #   class ScenarioFull(ScenarioPublic):
        #       answer_key: AnswerKey
        #       synth_seed: int | None = None
        #       synth_params: dict | None = None
        #       source_trade_id: int | None = None
        # symbol/session_date always real (un-blinded). Server-side only —
        # NEVER serialized to HTTP.
    create_attempt(payload: AttemptCreate) -> Attempt | None
        # None if payload.scenario_id is unknown (route maps to 404)
    get_attempt(attempt_id: int) -> Attempt | None
    append_attempt_actions(attempt_id: int, actions: list[AttemptAction]) -> Attempt | None
        # append-only event log; returns the updated Attempt, None if unknown
    list_attempts(scenario_id: int | None = None,
                  from_: date | None = None,
                  to: date | None = None) -> list[Attempt]
    finish_attempt(attempt_id: int, result: AttemptFinishResponse) -> Attempt | None
        # persists finished_at + score fields; returns the finished Attempt

quant — `app.analytics.training` (task 6.1.1, server-authoritative scoring):
    score_attempt(attempt: Attempt, scenario: ScenarioFull) -> AttemptFinishResponse

quant — `app.indicators.service` (task 6.0.4, causal bar-range slicing):
    compute_scenario_session(provider, scenario: ScenarioFull, settings,
                             blind: bool) -> ScenarioSessionResponse

quant — `app.analytics.leaks` (task 6.3.3, leak detection + drill playlist):
    detect_leaks(trades, fills, debriefs, attempts, scenarios) -> LeakReport
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import provider_dep, settings_dep
from app.config import Settings
from app.models.training import (
    Archetype,
    Attempt,
    AttemptCreate,
    AttemptEventsPatch,
    AttemptFinishResponse,
    DrillType,
    ScenarioCreate,
    ScenarioPublic,
    ScenarioSessionResponse,
)
from app.providers.base import DataProvider

router = APIRouter(tags=["training"])

_STORE_501 = "training store not implemented (data-eng task 6.0.2)"
_SLICER_501 = "scenario session slicer not implemented (quant task 6.0.4)"
_SCORER_501 = "process scorer not implemented (quant task 6.1.1)"
_LEAKS_501 = "leak detector not implemented (quant task 6.3.3)"
_MASTERY_501 = "mastery model not implemented (quant task 6.4.1)"


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

@router.get("/training/scenarios", response_model=list[ScenarioPublic])
def list_scenarios(
    archetype: Archetype | None = None,
    drill_type: DrillType | None = None,
    active: bool | None = None,
) -> list[ScenarioPublic]:
    try:
        from app.db.training_store import list_scenarios as _list
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    return _list(archetype=archetype, drill_type=drill_type, active=active)


@router.get("/training/scenarios/{scenario_id}", response_model=ScenarioPublic)
def get_scenario(scenario_id: int) -> ScenarioPublic:
    try:
        from app.db.training_store import get_scenario as _get
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    s = _get(scenario_id)
    if s is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return s


@router.post("/training/scenarios", response_model=ScenarioPublic)
def create_scenario(payload: ScenarioCreate) -> ScenarioPublic:
    """Authoring UI (6.1.3) and the debrief agent's create_scenario tool both
    post here — same validation path for both."""
    try:
        from app.db.training_store import create_scenario as _create
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    return _create(payload)


@router.get(
    "/training/scenarios/{scenario_id}/session",
    response_model=ScenarioSessionResponse,
)
def scenario_session(
    scenario_id: int,
    blind: bool | None = Query(None, description="override scenario.blind_default"),
    provider: DataProvider = Depends(provider_dep),
    settings: Settings = Depends(settings_dep),
) -> ScenarioSessionResponse:
    try:
        from app.db.training_store import get_scenario_full
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        from app.indicators.service import compute_scenario_session
    except ImportError:
        raise HTTPException(status_code=501, detail=_SLICER_501)
    scenario = get_scenario_full(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    try:
        return compute_scenario_session(
            provider,
            scenario,
            settings,
            blind=blind if blind is not None else scenario.blind_default,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail=_SLICER_501)


# --------------------------------------------------------------------------- #
# Attempts
# --------------------------------------------------------------------------- #

@router.post("/training/attempts", response_model=Attempt)
def create_attempt(payload: AttemptCreate) -> Attempt:
    try:
        from app.db.training_store import create_attempt as _create
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    attempt = _create(payload)
    if attempt is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return attempt


@router.patch("/training/attempts/{attempt_id}", response_model=Attempt)
def append_attempt_events(attempt_id: int, patch: AttemptEventsPatch) -> Attempt:
    """Append actions to the bar-indexed event log. Append-only by contract."""
    try:
        from app.db.training_store import append_attempt_actions
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    attempt = append_attempt_actions(attempt_id, patch.actions)
    if attempt is None:
        raise HTTPException(status_code=404, detail="attempt not found")
    return attempt


@router.post("/training/attempts/{attempt_id}/finish", response_model=AttemptFinishResponse)
def finish_attempt(attempt_id: int) -> AttemptFinishResponse:
    """The ONLY endpoint that ever returns the answer key. Scoring is
    server-authoritative (quant, app/analytics/training.py)."""
    try:
        from app.db.training_store import finish_attempt as _finish
        from app.db.training_store import get_attempt, get_scenario_full
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        from app.analytics.training import score_attempt
    except ImportError:
        raise HTTPException(status_code=501, detail=_SCORER_501)
    attempt = get_attempt(attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="attempt not found")
    scenario = get_scenario_full(attempt.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    try:
        result = score_attempt(attempt, scenario)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail=_SCORER_501)
    finished = _finish(attempt_id, result)
    if finished is not None:
        result.attempt = finished
    return result


@router.get("/training/attempts", response_model=list[Attempt])
def list_attempts(
    scenario_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
) -> list[Attempt]:
    try:
        from app.db.training_store import list_attempts as _list
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    return _list(scenario_id=scenario_id, from_=from_, to=to)


# --------------------------------------------------------------------------- #
# Leak detector + drill playlist prescription (task 6.3.3)
# --------------------------------------------------------------------------- #

@router.get("/training/leaks")
def get_leaks(provider: DataProvider = Depends(provider_dep)) -> dict:
    """Detected leaks + prescribed drill playlists.

    Reads: trades (real+sim, NOT training), fills, debriefs, attempts,
    active scenarios. All local. Same read-only guarantee as everything
    else in the app.
    """
    try:
        from app.analytics.leaks import detect_leaks
    except ImportError:
        raise HTTPException(status_code=501, detail=_LEAKS_501)
    try:
        from app.db.training_store import (
            list_debriefs,
            list_attempts as _list_attempts,
            list_scenarios as _list_scenarios,
        )
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    try:
        from app.ingest.query import list_trades
    except ImportError:
        # Fallback: no trades available (pre-M3 DB or missing module)
        list_trades = lambda: []  # noqa: E731

    trades = list_trades()        # default excludes context='training'
    fills: list = []              # list_fills not yet in ingest.query (v1)
    debriefs = list_debriefs()
    attempts = _list_attempts()
    scenarios = _list_scenarios(active=True)

    report = detect_leaks(
        trades=trades,
        fills=fills,
        debriefs=debriefs,
        attempts=attempts,
        scenarios=scenarios,
    )
    return report.to_dict()


# --------------------------------------------------------------------------- #
# Mastery model + spaced-repetition scheduler (task 6.4.1)                   #
# --------------------------------------------------------------------------- #

@router.get("/training/progress")
def get_progress() -> dict:
    """Per-archetype mastery levels + SM-2-lite due-review queue + streak.

    All signals derive from process_score (rule adherence + decision
    correctness). outcome_pnl is NEVER read — a user who follows rules and
    loses money advances; one who breaks rules and profits does not.

    Reads all attempts and all scenarios (active=None, for archetype mapping).
    """
    try:
        from app.analytics.mastery import compute_progress
    except ImportError:
        raise HTTPException(status_code=501, detail=_MASTERY_501)
    try:
        from app.db.training_store import (
            list_attempts as _list_attempts,
            list_scenarios as _list_scenarios,
        )
    except ImportError:
        raise HTTPException(status_code=501, detail=_STORE_501)
    from datetime import datetime, timezone

    attempts = _list_attempts()
    scenarios = _list_scenarios(active=None)  # all scenarios for archetype mapping

    # Enrich attempts with DB-persisted process_score so compute_progress can
    # see scores for finished attempts (list_attempts returns the Attempt model
    # without score fields — they're attached as dynamic attrs by enrichment).
    # Mirrors the pattern in app.analytics.leaks._enrich_attempts_from_db.
    try:
        from app.analytics.leaks import _enrich_attempts_from_db
        attempts = _enrich_attempts_from_db(attempts)
    except (ImportError, Exception):
        pass  # best-effort; compute_progress degrades gracefully with no scores

    report = compute_progress(attempts, scenarios, now=datetime.now(timezone.utc))
    return report.to_dict()
