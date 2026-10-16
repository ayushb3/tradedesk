"""Course concept-extraction orchestration (M8c, issue #54, PR-1).

Bridges the LLM generation seam (`app.llm.generate.generate_json`) and the seed
vocabulary: builds the prompt, calls the LLM, validates the returned dict against
small parse models, and DISCARDS any concept/link whose slug is not in the seed
set (the drift guard, PRD-M8 §7 risk #2). Returns None whenever the LLM seam
returns None (e.g. ``llm=none``, binary absent, API error) so the caller skips
the lesson and writes nothing.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.knowledge.gen_prompts import build_concept_prompt, build_sheet_prompt
from app.knowledge.seed import SeedVocab
from app.llm import generate as llm_generate

_ALLOWED_RELATIONS = {"related", "prerequisite", "contrast", "part_of"}


# --- Parse models for the raw LLM payload (permissive; validated then filtered) --- #

class _ConceptItem(BaseModel):
    slug: str
    weight: float = 1.0


class _LinkItem(BaseModel):
    a_slug: str
    b_slug: str
    relation: str


class _RawExtraction(BaseModel):
    concepts: list[_ConceptItem] = []
    links: list[_LinkItem] = []


@dataclass
class ConceptExtraction:
    """Drift-guarded extraction result for one lesson."""

    concepts: list[dict] = field(default_factory=list)  # [{slug, weight}]
    links: list[dict] = field(default_factory=list)     # [{a_slug, b_slug, relation}]


def extract_concepts_for_lesson(
    lesson: dict,
    seed: SeedVocab,
    *,
    settings: Settings | None = None,
    run_fn=None,
) -> ConceptExtraction | None:
    """Extract seed-vocabulary concepts for one lesson.

    Returns a ConceptExtraction (possibly empty) on success, or None when the LLM
    seam returned nothing (degradation path — caller does NOT mark the run).
    """
    system, user = build_concept_prompt(lesson, seed)
    payload = llm_generate.generate_json(
        system, user, settings=settings, run_fn=run_fn
    )
    if payload is None:
        return None

    try:
        raw = _RawExtraction.model_validate(payload)
    except ValidationError as exc:
        print(
            f"[knowledge-concepts] lesson {lesson.get('id')} payload shape mismatch: "
            f"{exc.error_count()} error(s) — treating as empty",
            file=sys.stderr,
        )
        return ConceptExtraction()

    allowed = seed.slugs()

    concepts: list[dict] = []
    seen_slugs: set[str] = set()
    for item in raw.concepts:
        if item.slug not in allowed:
            continue  # drift guard
        if item.slug in seen_slugs:
            continue  # dedupe
        seen_slugs.add(item.slug)
        # Clamp weight to the 0.0–1.0 salience range the prompt/schema specify.
        weight = item.weight if item.weight is not None else 1.0
        weight = max(0.0, min(1.0, float(weight)))
        concepts.append({"slug": item.slug, "weight": weight})

    links: list[dict] = []
    for link in raw.links:
        if link.a_slug not in allowed or link.b_slug not in allowed:
            continue  # drift guard
        if link.a_slug == link.b_slug:
            continue
        if link.relation not in _ALLOWED_RELATIONS:
            continue
        links.append(
            {"a_slug": link.a_slug, "b_slug": link.b_slug, "relation": link.relation}
        )

    return ConceptExtraction(concepts=concepts, links=links)


# --------------------------------------------------------------------------- #
# M8c — Study sheets (issue #54, PR-2)
# --------------------------------------------------------------------------- #


class _QuizParse(BaseModel):
    q: str = ""
    choices: list[str] = []
    answer_idx: int = 0
    explanation: str | None = None


class _SheetParse(BaseModel):
    """Permissive parse of the raw sheet payload — every field defaults so a
    partial/missing payload still validates; sanitization happens afterward."""

    summary_md: str = ""
    rules_md: str = ""
    diagram_mermaid: str | None = None
    quiz: list[_QuizParse] = []


@dataclass
class StudySheet:
    """A generated study sheet for one lesson (post-sanitization)."""

    summary_md: str = ""
    rules_md: str = ""
    diagram_mermaid: str | None = None
    quiz: list[dict] = field(default_factory=list)  # [{q, choices, answer_idx, explanation}]


def generate_sheet_for_lesson(
    lesson: dict,
    *,
    settings: Settings | None = None,
    run_fn=None,
) -> StudySheet | None:
    """Generate a study sheet for one lesson via the LLM seam.

    Returns a StudySheet on success (possibly with an empty quiz / null diagram),
    or None when the LLM seam returned nothing (degradation path — the caller does
    NOT write a sheet row, so a later real run reprocesses the lesson).
    """
    system, user = build_sheet_prompt(lesson)
    payload = llm_generate.generate_json(
        system, user, settings=settings, run_fn=run_fn
    )
    if payload is None:
        return None

    try:
        raw = _SheetParse.model_validate(payload)
    except ValidationError as exc:
        print(
            f"[knowledge-sheets] lesson {lesson.get('id')} payload shape mismatch: "
            f"{exc.error_count()} error(s) — treating as empty sheet",
            file=sys.stderr,
        )
        return StudySheet()

    # Sanitize quiz: keep only well-formed questions (2+ choices, in-range answer).
    quiz: list[dict] = []
    for item in raw.quiz:
        if len(item.choices) < 2:
            continue
        if not (0 <= item.answer_idx < len(item.choices)):
            continue
        if not (item.q or "").strip():
            continue
        quiz.append(
            {
                "q": item.q,
                "choices": list(item.choices),
                "answer_idx": item.answer_idx,
                "explanation": item.explanation,
            }
        )

    diagram = raw.diagram_mermaid
    if isinstance(diagram, str) and not diagram.strip():
        diagram = None

    return StudySheet(
        summary_md=raw.summary_md or "",
        rules_md=raw.rules_md or "",
        diagram_mermaid=diagram,
        quiz=quiz,
    )
