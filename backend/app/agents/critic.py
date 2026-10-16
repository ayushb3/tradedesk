"""Trade Critic orchestration (M9a, issue #58, R6–R11).

Mirrors `app.knowledge.generate.extract_concepts_for_lesson`: build the prompt, call
the single generation seam (`app.llm.generate.generate_json`), permissively
parse the raw payload, then apply DRIFT GUARDS before returning a validated
`CriticResult`. Returns None when the LLM seam returns nothing (degradation) or
the payload is total garbage, so the route degrades clean. Logs to stderr only;
never raises out.

Guarantees:
- adherence coerced to the three-valued {pass, fail, unclear}; unknown/missing → "unclear".
- source_citation resolved from the model's `source_citation_lesson_id` against
  the lessons actually in context (R3); unverified → None.
- FAITHFULNESS heuristic (PRD risk #1): a cited lesson whose text has essentially
  no lexical overlap with the rule/note is DEMOTED to unverified (None).
- evidence_bar_ref kept only when it parses as a timestamp, else None.
- prescription.readings filtered to real lesson ids; drill_id validated against
  the training store (lazily); no P&L field ever emitted.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.agents.critic_prompts import build_critic_prompt
from app.knowledge import store
from app.llm import generate as llm_generate
from app.llm.base import DebriefContext
from app.models.agents import (
    KnowledgeContext,
    CriticPhaseVerdicts,
    CriticPrescription,
    CriticResult,
    CriticRuleCheck,
)
from app.models.knowledge import KnowledgeSearchHit
from app.models.training import ScenarioCreate

# Faithfulness threshold: a citation survives when the cited lesson text shares
# at least this many DISTINCT significant tokens (>=4 chars, alnum) with the
# rule+note text. Deliberately conservative — we only want to catch citations
# with essentially ZERO lexical support, not police paraphrase. One shared
# significant token is enough to keep the citation; zero drops it.
_MIN_SHARED_TOKENS = 1
_MIN_TOKEN_LEN = 4

_ADHERENCE_VALUES = {"pass", "fail", "unclear"}


# --- Permissive parse models (all fields defaulted) ------------------------- #

class _PhaseVerdictsParse(BaseModel):
    setup: str = ""
    entry: str = ""
    management: str = ""
    exit: str = ""


class _RuleCheckParse(BaseModel):
    rule: str = ""
    adherence: str = "unclear"
    source_citation_lesson_id: int | None = None
    evidence_bar_ref: str | None = None
    note: str | None = None


class _PrescriptionParse(BaseModel):
    drill_id: int | None = None
    # Permissive: keep the raw dict so a malformed drill_spec never fails the
    # whole critique — we parse it into ScenarioCreate downstream, dropping to
    # None on ValidationError.
    drill_spec: dict | None = None
    readings: list[int] = Field(default_factory=list)


class _CriticParse(BaseModel):
    verdict_by_phase: _PhaseVerdictsParse = Field(default_factory=_PhaseVerdictsParse)
    rule_checks: list[_RuleCheckParse] = Field(default_factory=list)
    prescription: _PrescriptionParse = Field(default_factory=_PrescriptionParse)


@dataclass
class CriticInputs:
    """Bundle passed to `critique_trade`. The PM's route constructs a structurally
    compatible placeholder (attrs: trade_ctx, knowledge[, settings]); only trade_ctx and
    knowledge are required here."""

    trade_ctx: DebriefContext
    knowledge: KnowledgeContext
    debrief: dict | None = None


def _significant_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(t) >= _MIN_TOKEN_LEN
    }


def _is_faithful(rule: str, note: str | None, hit: KnowledgeSearchHit) -> bool:
    """Cheap word-set overlap heuristic between the check text and the cited hit.

    Returns True when the rule/note shares at least `_MIN_SHARED_TOKENS`
    significant tokens with the cited passage — i.e. the citation has SOME real
    lexical support. Conservative by design (see module docstring)."""
    check_tokens = _significant_tokens(rule) | _significant_tokens(note or "")
    if not check_tokens:
        # Nothing to compare against — don't punish a terse note; keep it.
        return True
    hit_tokens = _significant_tokens(hit.text) | _significant_tokens(
        hit.lesson_title or ""
    )
    return len(check_tokens & hit_tokens) >= _MIN_SHARED_TOKENS


def _parse_ts(value) -> str | None:
    """Keep an ISO-8601 timestamp string if it parses, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        # Python 3.11+ fromisoformat handles trailing 'Z'.
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        return None


def _resolve_drill_id(drill_id: int | None) -> int | None:
    """Validate a drill_id against the training store (lazily). None if the
    module/fn is absent or the scenario doesn't exist."""
    if drill_id is None:
        return None
    try:
        from app.db import training_store
    except ImportError:
        return None
    get_scenario = getattr(training_store, "get_scenario", None)
    if get_scenario is None:
        return None
    try:
        return drill_id if get_scenario(drill_id) is not None else None
    except Exception:  # never let store errors escape the critic
        return None


def _parse_drill_spec(raw, trade_ctx) -> ScenarioCreate | None:
    """Parse the model's raw drill_spec dict into a ScenarioCreate, or None.

    Degrade-clean: a malformed/missing spec returns None and never raises. When
    the model omits the trade linkage, we backfill symbol/session_date/
    source_trade_id from the trade so the authored drill references the real
    tape (mirrors finish_debrief's debrief-emitted scenario shape)."""
    if not isinstance(raw, dict) or not raw:
        return None
    spec = dict(raw)  # shallow copy — don't mutate the parsed payload
    trade = getattr(trade_ctx, "trade", None)
    if trade is not None:
        spec.setdefault("symbol", getattr(trade, "symbol", None))
        session_date = getattr(trade, "session_date", None)
        if session_date is not None:
            spec.setdefault("session_date", session_date)
        trade_id = getattr(trade, "id", None)
        if trade_id is not None:
            spec.setdefault("source_trade_id", trade_id)
    try:
        return ScenarioCreate.model_validate(spec)
    except ValidationError as exc:
        print(
            f"[critic] drill_spec shape mismatch: {exc.error_count()} error(s) "
            "— dropping drill_spec",
            file=sys.stderr,
        )
        return None


def critique_trade(
    inputs,
    *,
    settings: Settings | None = None,
    run_fn=None,
) -> CriticResult | None:
    """Critique a trade against course rules. Returns a validated CriticResult, or
    None on degradation (LLM seam returned nothing, or the payload was garbage).

    ``inputs`` need only expose ``.trade_ctx`` (DebriefContext) and ``.knowledge``
    (KnowledgeContext); an optional ``.debrief`` is threaded into the prompt.
    """
    trade_ctx = inputs.trade_ctx
    knowledge = inputs.knowledge
    debrief = getattr(inputs, "debrief", None)

    system, user = build_critic_prompt(trade_ctx, knowledge, debrief=debrief)
    payload = llm_generate.generate_json(
        system, user, settings=settings, run_fn=run_fn
    )
    if payload is None:
        return None

    try:
        raw = _CriticParse.model_validate(payload)
    except ValidationError as exc:
        print(
            f"[critic] payload shape mismatch: {exc.error_count()} error(s) "
            "— degrading to None",
            file=sys.stderr,
        )
        return None

    # Index the lessons actually in context by lesson_id (R3 source of truth).
    hits_by_lesson: dict[int, KnowledgeSearchHit] = {
        h.lesson_id: h for h in (knowledge.hits if knowledge else [])
    }

    rule_checks: list[CriticRuleCheck] = []
    for rc in raw.rule_checks:
        rule = (rc.rule or "").strip()
        if not rule:
            continue  # a rule-check with no rule text carries no signal
        adherence = rc.adherence if rc.adherence in _ADHERENCE_VALUES else "unclear"

        # Resolve + faithfulness-check the citation (R3, PRD risk #1).
        citation: KnowledgeSearchHit | None = None
        lid = rc.source_citation_lesson_id
        if lid is not None:
            hit = hits_by_lesson.get(lid)
            if hit is not None and _is_faithful(rule, rc.note, hit):
                citation = hit
            # else: cited lesson not in context OR unfaithful → unverified (None)

        rule_checks.append(
            CriticRuleCheck(
                rule=rule,
                adherence=adherence,  # type: ignore[arg-type]
                source_citation=citation,
                evidence_bar_ref=_parse_ts(rc.evidence_bar_ref),
                note=(rc.note or None),
            )
        )

    # Prescription: filter readings to real lessons; validate drill_id.
    readings: list[int] = []
    seen_readings: set[int] = set()
    for lid in raw.prescription.readings:
        if lid in seen_readings:
            continue
        if store.lesson_exists(lid):
            seen_readings.add(lid)
            readings.append(lid)

    prescription = CriticPrescription(
        drill_id=_resolve_drill_id(raw.prescription.drill_id),
        readings=readings,
    )
    # drill_id wins: only surface a drill_spec when no valid existing drill was
    # referenced (R10 — EITHER an existing drill OR a new spec, never both).
    if prescription.drill_id is None:
        prescription.drill_spec = _parse_drill_spec(
            raw.prescription.drill_spec, trade_ctx
        )

    verdicts = CriticPhaseVerdicts(
        setup=raw.verdict_by_phase.setup or "",
        entry=raw.verdict_by_phase.entry or "",
        management=raw.verdict_by_phase.management or "",
        exit=raw.verdict_by_phase.exit or "",
    )

    return CriticResult(
        verdict_by_phase=verdicts,
        rule_checks=rule_checks,
        prescription=prescription,
    )
