"""Training-module persistence (M6 task 6.0.2).

Implements the exact function signatures documented in the route file docstrings:
  - app.api.routes.training (scenarios + attempts)
  - app.api.routes.debriefs  (debrief interview sessions)

Two hard invariants preserved here:

1. **Answer-key hiding** — `get_scenario` BLANKS `symbol` and `session_date`
   when `scenario.blind_default` is True, so callers never leak the tape
   identity on public endpoints. `get_scenario_full` always returns them
   (server-side only, NEVER serialized to HTTP).

2. **Append-only action log** — `append_attempt_actions` extends `actions_json`
   without ever overwriting earlier events. The log is bar-indexed and
   replayable (design principle §2 of PLAN-M6-TRAINING.md).

ScenarioFull is defined here (not in models.training) per the route contract:

    class ScenarioFull(ScenarioPublic):
        answer_key: AnswerKey
        synth_seed: int | None = None
        synth_params: dict | None = None
        source_trade_id: int | None = None
    # symbol/session_date always real (un-blinded). Server-side only.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import (
    debriefs as debriefs_t,
    exercise_attempts as attempts_t,
    scenarios as scenarios_t,
    trade_enrichment as enrich_t,
    trades as trades_t,
)
from app.models.training import (
    AnswerKey,
    Archetype,
    Attempt,
    AttemptAction,
    AttemptCreate,
    AttemptFinishResponse,
    Debrief,
    DebriefArtifacts,
    DebriefCreate,
    DebriefFinish,
    DebriefStep,
    DebriefVerdict,
    DrillType,
    MoodCheckin,
    ScenarioAnnotation,
    ScenarioCreate,
    ScenarioPublic,
    ScenarioSource,
)
from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# ScenarioFull — server-side only, NEVER serialized to HTTP                   #
# --------------------------------------------------------------------------- #

class ScenarioFull(ScenarioPublic):
    """Full scenario including the answer key and internal fields.

    Used by:
    - finish_attempt route (needs answer_key to call score_attempt)
    - scenario_session route (needs symbol/session_date un-blinded for quant slicer)

    NEVER returned via any HTTP endpoint. Server-side only.
    """

    answer_key: AnswerKey
    synth_seed: int | None = None
    synth_params: dict | None = None
    source_trade_id: int | None = None
    # symbol and session_date are ALWAYS real here (overrides blind blanking)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_annotations(raw: Any) -> list[ScenarioAnnotation]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [ScenarioAnnotation(**a) for a in raw]


def _parse_answer_key(raw: Any) -> AnswerKey:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return AnswerKey(**raw)


def _parse_actions(raw: Any) -> list[AttemptAction]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [AttemptAction(**a) for a in raw]


def _row_to_scenario_full(row) -> ScenarioFull:
    m = row._mapping
    return ScenarioFull(
        id=m["id"],
        title=m["title"],
        archetype=Archetype(m["archetype"]),
        source_type=ScenarioSource(m["source_type"]),
        drill_type=DrillType(m["drill_type"]),
        symbol=m["symbol"],
        session_date=m["session_date"],
        start_bar=m["start_bar"],
        decision_bar=m["decision_bar"],
        end_bar=m["end_bar"],
        annotations=_parse_annotations(m["annotations_json"]),
        checklist_template_id=m["checklist_template_id"],
        blind_default=bool(m["blind_default"]),
        active=bool(m["active"]),
        created_at=m["created_at"],
        # ScenarioFull-only fields
        answer_key=_parse_answer_key(m["answer_key_json"]),
        synth_seed=m["synth_seed"],
        synth_params=m["synth_params_json"] if m["synth_params_json"] else None,
        source_trade_id=m["source_trade_id"],
    )


def _full_to_public(full: ScenarioFull, blind: bool) -> ScenarioPublic:
    """Convert ScenarioFull to public shape, blanking tape identity when blind."""
    return ScenarioPublic(
        id=full.id,
        title=full.title,
        archetype=full.archetype,
        source_type=full.source_type,
        drill_type=full.drill_type,
        symbol=None if blind else full.symbol,
        session_date=None if blind else full.session_date,
        start_bar=full.start_bar,
        decision_bar=full.decision_bar,
        end_bar=full.end_bar,
        annotations=full.annotations,
        checklist_template_id=full.checklist_template_id,
        blind_default=full.blind_default,
        active=full.active,
        created_at=full.created_at,
    )


def _row_to_attempt(row) -> Attempt:
    m = row._mapping
    mood = MoodCheckin(m["mood_checkin"]) if m["mood_checkin"] else None
    return Attempt(
        id=m["id"],
        scenario_id=m["scenario_id"],
        started_at=m["started_at"],
        finished_at=m["finished_at"],
        mood_checkin=mood,
        actions=_parse_actions(m["actions_json"]),
    )


def _row_to_debrief(row) -> Debrief:
    m = row._mapping
    verdict = DebriefVerdict(m["verdict"]) if m["verdict"] else None
    transcript = m["transcript_json"] or []
    if isinstance(transcript, str):
        transcript = json.loads(transcript)
    plan = m["plan_json"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    feelings = m["feelings_json"] or []
    if isinstance(feelings, str):
        feelings = json.loads(feelings)
    return Debrief(
        id=m["id"],
        trade_id=m["trade_id"],
        llm_backend=m["llm_backend"],
        step=DebriefStep(m["step"]),
        transcript=transcript,
        plan=plan,
        feelings=feelings,
        verdict=verdict,
        scenario_id=m["scenario_id"],
        created_at=m["created_at"],
    )


# --------------------------------------------------------------------------- #
# Scenarios                                                                    #
# --------------------------------------------------------------------------- #

def create_scenario(
    payload: ScenarioCreate,
    engine: Engine | None = None,
) -> ScenarioPublic:
    """Insert a new scenario and return the public (possibly blinded) shape."""
    engine = engine or get_engine()
    init_db(engine)

    now = _utcnow()
    annotations_raw = [a.model_dump() for a in payload.annotations]
    answer_key_raw = payload.answer_key.model_dump()

    with engine.begin() as conn:
        res = conn.execute(
            scenarios_t.insert().values(
                title=payload.title,
                archetype=payload.archetype.value,
                source_type=payload.source_type.value,
                drill_type=payload.drill_type.value,
                symbol=payload.symbol,
                session_date=payload.session_date,
                synth_seed=payload.synth_seed,
                synth_params_json=payload.synth_params,
                start_bar=payload.start_bar,
                decision_bar=payload.decision_bar,
                end_bar=payload.end_bar,
                annotations_json=annotations_raw,
                answer_key_json=answer_key_raw,
                checklist_template_id=payload.checklist_template_id,
                source_trade_id=payload.source_trade_id,
                blind_default=payload.blind_default,
                active=True,
                created_at=now,
            )
        )
        scenario_id = int(res.inserted_primary_key[0])

    full = get_scenario_full(scenario_id, engine=engine)
    assert full is not None  # just inserted
    return _full_to_public(full, blind=full.blind_default)


def list_scenarios(
    archetype: Archetype | None = None,
    drill_type: DrillType | None = None,
    active: bool | None = None,
    engine: Engine | None = None,
) -> list[ScenarioPublic]:
    """Filtered list of scenarios in public (possibly blinded) shape."""
    engine = engine or get_engine()
    init_db(engine)

    stmt = select(scenarios_t)
    if archetype is not None:
        stmt = stmt.where(scenarios_t.c.archetype == archetype.value)
    if drill_type is not None:
        stmt = stmt.where(scenarios_t.c.drill_type == drill_type.value)
    if active is not None:
        stmt = stmt.where(scenarios_t.c.active.is_(active))
    stmt = stmt.order_by(scenarios_t.c.id.desc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    result = []
    for row in rows:
        full = _row_to_scenario_full(row)
        result.append(_full_to_public(full, blind=full.blind_default))
    return result


def get_scenario(
    scenario_id: int,
    engine: Engine | None = None,
) -> ScenarioPublic | None:
    """Return the public scenario shape, blanking symbol/session_date when blind_default=True.

    Blind-mode leak prevention: callers (and the HTTP layer) must NEVER see the
    tape identity on a blind scenario. The full details are only in ScenarioFull
    (server-side only) or exposed by finish_attempt.
    """
    full = get_scenario_full(scenario_id, engine=engine)
    if full is None:
        return None
    return _full_to_public(full, blind=full.blind_default)


def get_scenario_full(
    scenario_id: int,
    engine: Engine | None = None,
) -> ScenarioFull | None:
    """Return the full scenario including answer_key, symbol/session_date un-blinded.

    SERVER-SIDE ONLY. Never serialize this to an HTTP response.
    """
    engine = engine or get_engine()
    init_db(engine)

    with engine.connect() as conn:
        row = conn.execute(
            select(scenarios_t).where(scenarios_t.c.id == scenario_id)
        ).first()

    if row is None:
        return None
    return _row_to_scenario_full(row)


# --------------------------------------------------------------------------- #
# Attempts                                                                     #
# --------------------------------------------------------------------------- #

def create_attempt(
    payload: AttemptCreate,
    engine: Engine | None = None,
) -> Attempt | None:
    """Open a new attempt. Returns None if scenario_id is unknown (route maps to 404)."""
    engine = engine or get_engine()
    init_db(engine)

    # Validate that the scenario exists before creating the attempt.
    scenario = get_scenario_full(payload.scenario_id, engine=engine)
    if scenario is None:
        return None

    now = _utcnow()
    mood = payload.mood_checkin.value if payload.mood_checkin else None

    with engine.begin() as conn:
        res = conn.execute(
            attempts_t.insert().values(
                scenario_id=payload.scenario_id,
                started_at=now,
                finished_at=None,
                mood_checkin=mood,
                actions_json=[],
                process_score=None,
                outcome_pnl=None,
                checklist_json=None,
                verdict=None,
                notes=None,
            )
        )
        attempt_id = int(res.inserted_primary_key[0])

    return get_attempt(attempt_id, engine=engine)


def get_attempt(
    attempt_id: int,
    engine: Engine | None = None,
) -> Attempt | None:
    """Return an attempt by id, or None if it doesn't exist."""
    engine = engine or get_engine()
    init_db(engine)

    with engine.connect() as conn:
        row = conn.execute(
            select(attempts_t).where(attempts_t.c.id == attempt_id)
        ).first()

    if row is None:
        return None
    return _row_to_attempt(row)


def append_attempt_actions(
    attempt_id: int,
    actions: list[AttemptAction],
    engine: Engine | None = None,
) -> Attempt | None:
    """Append new bar-indexed actions to the event log (never rewrite). Returns
    the updated Attempt, or None if the attempt is unknown.

    Append-only is enforced: we load the existing list, extend it, and write
    back. SQLite has no native array-append, so read-modify-write in a
    transaction is the correct pattern here.
    """
    engine = engine or get_engine()
    init_db(engine)

    with engine.begin() as conn:
        row = conn.execute(
            select(attempts_t).where(attempts_t.c.id == attempt_id)
        ).first()
        if row is None:
            return None

        m = row._mapping
        existing = _parse_actions(m["actions_json"])
        updated = existing + actions
        updated_raw = [a.model_dump(mode="json") for a in updated]

        conn.execute(
            attempts_t.update()
            .where(attempts_t.c.id == attempt_id)
            .values(actions_json=updated_raw)
        )

    return get_attempt(attempt_id, engine=engine)


def list_attempts(
    scenario_id: int | None = None,
    from_: date | None = None,
    to: date | None = None,
    engine: Engine | None = None,
) -> list[Attempt]:
    """List attempts with optional filters on scenario and date range."""
    engine = engine or get_engine()
    init_db(engine)

    stmt = select(attempts_t)
    if scenario_id is not None:
        stmt = stmt.where(attempts_t.c.scenario_id == scenario_id)
    if from_ is not None:
        # started_at is a DateTime; compare date portion via cast to text prefix
        stmt = stmt.where(attempts_t.c.started_at >= datetime(from_.year, from_.month, from_.day))
    if to is not None:
        # include the whole 'to' day
        from datetime import timedelta
        next_day = datetime(to.year, to.month, to.day) + timedelta(days=1)
        stmt = stmt.where(attempts_t.c.started_at < next_day)
    stmt = stmt.order_by(attempts_t.c.id.desc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_row_to_attempt(r) for r in rows]


def finish_attempt(
    attempt_id: int,
    result: AttemptFinishResponse,
    engine: Engine | None = None,
) -> Attempt | None:
    """Persist finish data (finished_at + score fields) from the server-computed
    AttemptFinishResponse. Returns the updated Attempt, or None if unknown.

    The score is server-authoritative (quant task 6.1.1). This function just
    persists what was computed; it does not recompute anything.
    """
    engine = engine or get_engine()
    init_db(engine)

    now = _utcnow()
    checklist_raw = result.checklist if result.checklist else None

    with engine.begin() as conn:
        row = conn.execute(
            select(attempts_t.c.id).where(attempts_t.c.id == attempt_id)
        ).first()
        if row is None:
            return None

        conn.execute(
            attempts_t.update()
            .where(attempts_t.c.id == attempt_id)
            .values(
                finished_at=now,
                process_score=result.process_score,
                outcome_pnl=result.outcome_pnl,
                checklist_json=checklist_raw,
                verdict=result.verdict,
            )
        )

    return get_attempt(attempt_id, engine=engine)


# --------------------------------------------------------------------------- #
# Debriefs                                                                     #
# --------------------------------------------------------------------------- #

def create_debrief(
    payload: DebriefCreate,
    llm_backend: str,
    engine: Engine | None = None,
) -> Debrief | None:
    """Create a new debrief session. Returns None if trade_id is unknown (route maps to 404)."""
    engine = engine or get_engine()
    init_db(engine)

    # Validate that the trade exists.
    with engine.connect() as conn:
        trade_row = conn.execute(
            select(trades_t.c.id).where(trades_t.c.id == payload.trade_id)
        ).first()
    if trade_row is None:
        return None

    now = _utcnow()
    with engine.begin() as conn:
        res = conn.execute(
            debriefs_t.insert().values(
                trade_id=payload.trade_id,
                llm_backend=llm_backend,
                step=DebriefStep.setup.value,
                transcript_json=[],
                plan_json=None,
                feelings_json=[],
                verdict=None,
                scenario_id=None,
                created_at=now,
            )
        )
        debrief_id = int(res.inserted_primary_key[0])

    return get_debrief(debrief_id, engine=engine)


def get_debrief(
    debrief_id: int,
    engine: Engine | None = None,
) -> Debrief | None:
    """Return a debrief by id, or None if not found."""
    engine = engine or get_engine()
    init_db(engine)

    with engine.connect() as conn:
        row = conn.execute(
            select(debriefs_t).where(debriefs_t.c.id == debrief_id)
        ).first()

    if row is None:
        return None
    return _row_to_debrief(row)


def append_debrief_transcript(
    debrief_id: int,
    entries: list[dict],
    engine: Engine | None = None,
) -> Debrief | None:
    """Append transcript entries (append-only). Also advances `step` to match the
    latest agent turn's step, so `debrief.step` always reflects the current
    interview position.

    entries format: [{"role": "agent"|"user", "step": str, "text": str}]
    Returns the updated Debrief, or None if debrief is unknown.
    """
    engine = engine or get_engine()
    init_db(engine)

    with engine.begin() as conn:
        row = conn.execute(
            select(debriefs_t).where(debriefs_t.c.id == debrief_id)
        ).first()
        if row is None:
            return None

        m = row._mapping
        existing = m["transcript_json"] or []
        if isinstance(existing, str):
            existing = json.loads(existing)
        updated = existing + entries

        # Advance step to the latest agent turn's step value.
        new_step = m["step"]
        for entry in entries:
            if entry.get("role") == "agent" and entry.get("step"):
                new_step = entry["step"]

        conn.execute(
            debriefs_t.update()
            .where(debriefs_t.c.id == debrief_id)
            .values(transcript_json=updated, step=new_step)
        )

    return get_debrief(debrief_id, engine=engine)


def list_debriefs(
    trade_id: int | None = None,
    engine: Engine | None = None,
) -> list[Debrief]:
    """List debriefs, optionally filtered by trade_id."""
    engine = engine or get_engine()
    init_db(engine)

    stmt = select(debriefs_t)
    if trade_id is not None:
        stmt = stmt.where(debriefs_t.c.trade_id == trade_id)
    stmt = stmt.order_by(debriefs_t.c.id.desc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_row_to_debrief(r) for r in rows]


def finish_debrief(
    debrief_id: int,
    finish: DebriefFinish,
    engine: Engine | None = None,
) -> DebriefArtifacts | None:
    """Persist finish artifacts and optionally emit a scenario.

    Steps:
    1. Load the debrief; return None if unknown.
    2. Extract plan/feelings/verdict from the transcript (last agent-structured
       entries). For now these may be None if the interview hadn't gathered them
       (the form backend will populate them on individual turn calls).
    3. Write enrichment to the trade via the existing `_upsert_enrichment` helper
       in ingest.query (DRY — do NOT duplicate that logic).
    4. If finish.emit_scenario, call create_scenario with a minimal payload derived
       from the trade's symbol/session_date. The scenario's answer_key comes from
       the debrief's plan_json when available.
    5. Update the debrief row with verdict and scenario_id, step='done'.
    6. Return DebriefArtifacts.
    """
    engine = engine or get_engine()
    init_db(engine)

    with engine.connect() as conn:
        row = conn.execute(
            select(debriefs_t).where(debriefs_t.c.id == debrief_id)
        ).first()
    if row is None:
        return None

    debrief = _row_to_debrief(row)

    # Extract verdict (and other artifacts) from the transcript.
    # The form backend (TRADEDESK_LLM=none) never writes structured fields to
    # individual debrief rows during /turn calls — it only appends raw transcript
    # entries.  finish_debrief is the first place we summarise them into
    # verdict/feelings/plan.  If the debrief row already has a verdict set (e.g.
    # an LLM backend persisted it directly) that takes priority; otherwise we
    # parse the transcript via summarize_transcript.
    verdict = debrief.verdict
    if verdict is None and debrief.transcript:
        try:
            from app.llm.form import summarize_transcript
            _enrich_delta, transcript_verdict, _feelings, _plan = (
                summarize_transcript(debrief.transcript)
            )
            if transcript_verdict is not None:
                verdict = transcript_verdict
        except Exception:
            # summarize_transcript is best-effort; don't block finish.
            pass

    # Write enrichment to the trade — use the shared helper from ingest.query.
    # We write whatever notes/grade the transcript has produced. For now we write
    # a minimal enrichment marker so the trade is linked to this debrief.
    enrichment_saved = False
    try:
        from app.ingest.query import _upsert_enrichment
        from app.models.trades import TradeUpdate

        # Build an enrichment patch from the debrief. If the debrief has a plan
        # (with notes), use that. Otherwise leave fields as-is.
        patch = TradeUpdate()
        if debrief.plan and isinstance(debrief.plan, dict):
            notes_str = debrief.plan.get("notes")
            if notes_str:
                patch.notes = notes_str

        with engine.begin() as conn:
            _upsert_enrichment(conn, debrief.trade_id, patch)
        enrichment_saved = True
    except Exception:
        # Enrichment is best-effort at this stage. The route handles this
        # gracefully — DebriefArtifacts.enrichment_saved will be False.
        enrichment_saved = False

    # Optionally emit a scenario from this debrief.
    emitted_scenario_id: int | None = None
    if finish.emit_scenario:
        # Load the trade to get symbol/session_date for the scenario.
        with engine.connect() as conn:
            trade_row = conn.execute(
                select(trades_t).where(trades_t.c.id == debrief.trade_id)
            ).first()

        if trade_row is not None:
            tm = trade_row._mapping
            # Build a minimal scenario payload. The answer key defaults to an
            # empty "skip" action — the authoring UI (6.1.3) will refine it.
            # For debrief-emitted scenarios the important thing is to create the
            # record with source_type='debrief' linking back to the trade.
            from app.models.training import AttemptActionType, AnswerKey as AK, ScenarioCreate as SC

            answer_key = AK(
                correct_actions=[AttemptActionType.skip],
                explanation="(debrief-emitted — fill in via authoring UI)",
            )

            scenario_payload = SC(
                title=f"Debrief drill: {tm['symbol']} {tm['session_date']}",
                archetype=Archetype.custom,
                source_type=ScenarioSource.debrief,
                drill_type=DrillType.decision_point,
                symbol=tm["symbol"],
                session_date=tm["session_date"],
                start_bar=0,
                answer_key=answer_key,
                source_trade_id=debrief.trade_id,
                blind_default=False,
            )
            new_scenario = create_scenario(scenario_payload, engine=engine)
            emitted_scenario_id = new_scenario.id

    # Persist the finish state on the debrief row.
    with engine.begin() as conn:
        conn.execute(
            debriefs_t.update()
            .where(debriefs_t.c.id == debrief_id)
            .values(
                verdict=verdict.value if isinstance(verdict, DebriefVerdict) else verdict,
                scenario_id=emitted_scenario_id,
                step=DebriefStep.done.value,
            )
        )

    return DebriefArtifacts(
        enrichment_saved=enrichment_saved,
        verdict=verdict if isinstance(verdict, DebriefVerdict) else None,
        scenario_id=emitted_scenario_id,
    )
