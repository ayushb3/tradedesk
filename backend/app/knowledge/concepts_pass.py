"""Course concept-extraction pass orchestration (M8c, issue #54, PR-1).

Mirrors `app.knowledge.ingest.run_ingest`: pure orchestration, driven by the CLI at
`backend/scripts/extract_knowledge_concepts.py`. For each ingested lesson it:

  - seeds the controlled vocabulary first (idempotent),
  - skips when `knowledge_concept_runs.content_hash` matches the current transcript
    hash (unless ``force``),
  - otherwise calls the LLM extraction seam; on None (e.g. ``llm=none``) it
    counts ``skipped_no_llm`` and does NOT mark the run (so a later real run
    reprocesses it),
  - else writes the drift-guarded concepts + links and marks the run.

A lesson may legitimately extract zero concepts; completion is tracked via
`knowledge_concept_runs`, not `lesson_concepts`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.knowledge import store
from app.knowledge.generate import extract_concepts_for_lesson
from app.knowledge.seed import load_seed, seed_concepts_into_db


@dataclass
class ConceptPassReport:
    total_lessons: int = 0
    seeded_concepts: int = 0
    processed: int = 0             # lessons the LLM actually processed
    skipped_unchanged: int = 0     # resumability: hash matched
    skipped_no_llm: int = 0        # LLM seam returned None (e.g. llm=none)
    concepts_written: int = 0
    links_written: int = 0
    model: str = ""
    errors: list[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"lessons:               {self.total_lessons}",
            f"seeded concepts:       {self.seeded_concepts}",
            f"processed (llm):       {self.processed}",
            f"skipped (unchanged):   {self.skipped_unchanged}",
            f"skipped (no llm):      {self.skipped_no_llm}",
            f"lesson-concepts written: {self.concepts_written}",
            f"concept links written: {self.links_written}",
            f"model:                 {self.model or '(none)'}",
            f"errors:                {len(self.errors)} {self.errors or ''}".rstrip(),
        ]
        return "\n".join(lines)


def run_concept_extraction(
    *,
    engine: Engine | None = None,
    settings: Settings | None = None,
    run_fn=None,
    force: bool = False,
    limit: int | None = None,
) -> ConceptPassReport:
    """Run the concept-extraction pass over all ingested lessons. Idempotent /
    resumable via `knowledge_concept_runs.content_hash`."""
    settings = settings or get_settings()
    model = settings.llm

    report = ConceptPassReport(model=model)

    # Always seed the controlled vocabulary first (idempotent).
    seed = load_seed()
    report.seeded_concepts = seed_concepts_into_db(engine=engine)

    lessons = store.list_lessons(engine=engine)
    lessons = sorted(lessons, key=lambda r: r["id"])
    if limit:
        lessons = lessons[:limit]
    report.total_lessons = len(lessons)

    prior = {} if force else store.concept_run_hashes(engine=engine)

    for meta in lessons:
        lesson_id = meta["id"]
        full = store.get_lesson_full(lesson_id, engine=engine)
        if full is None:
            report.errors.append(lesson_id)
            continue

        chash = store.content_hash(full["raw_text"])
        if not force and prior.get(lesson_id) == chash:
            report.skipped_unchanged += 1
            continue

        extraction = extract_concepts_for_lesson(
            full, seed, settings=settings, run_fn=run_fn
        )
        if extraction is None:
            # LLM seam degraded to nothing — do NOT mark the run so a later real
            # run reprocesses this lesson.
            report.skipped_no_llm += 1
            continue

        n_concepts = store.replace_lesson_concepts(
            lesson_id, extraction.concepts, engine=engine
        )
        n_links = store.upsert_concept_links(extraction.links, engine=engine)
        store.mark_concept_run(lesson_id, chash, model, engine=engine)

        report.processed += 1
        report.concepts_written += n_concepts
        report.links_written += n_links

    return report
