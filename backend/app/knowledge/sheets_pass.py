"""Course study-sheet generation pass orchestration (M8c, issue #54, PR-2).

Mirrors `app.knowledge.concepts_pass.run_concept_extraction`: pure orchestration, driven
by the CLI at `backend/scripts/generate_knowledge_sheets.py`. For each ingested lesson it:

  - skips when `lesson_sheets.content_hash` matches the current transcript hash
    (unless ``force``),
  - otherwise calls the LLM sheet-generation seam; on None (e.g. ``llm=none``) it
    counts ``skipped_no_llm`` and does NOT write a sheet row (so a later real run
    reprocesses it),
  - else upserts the sheet. The sheet's `content_hash` IS the resumability marker
    — a row always exists once generated, so no separate run-marker table is
    needed (unlike concepts, where a lesson can legitimately have zero concepts).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.knowledge import store
from app.knowledge.generate import generate_sheet_for_lesson


@dataclass
class SheetPassReport:
    total_lessons: int = 0
    processed: int = 0             # lessons the LLM actually processed
    skipped_unchanged: int = 0     # resumability: hash matched
    skipped_no_llm: int = 0        # LLM seam returned None (e.g. llm=none)
    generated: int = 0             # sheet rows written
    model: str = ""
    errors: list[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"lessons:               {self.total_lessons}",
            f"processed (llm):       {self.processed}",
            f"skipped (unchanged):   {self.skipped_unchanged}",
            f"skipped (no llm):      {self.skipped_no_llm}",
            f"sheets written:        {self.generated}",
            f"model:                 {self.model or '(none)'}",
            f"errors:                {len(self.errors)} {self.errors or ''}".rstrip(),
        ]
        return "\n".join(lines)


def run_sheet_generation(
    *,
    engine: Engine | None = None,
    settings: Settings | None = None,
    run_fn=None,
    force: bool = False,
    limit: int | None = None,
) -> SheetPassReport:
    """Run the study-sheet generation pass over all ingested lessons. Idempotent /
    resumable via `lesson_sheets.content_hash`."""
    settings = settings or get_settings()
    model = settings.llm

    report = SheetPassReport(model=model)

    lessons = store.list_lessons(engine=engine)
    lessons = sorted(lessons, key=lambda r: r["id"])
    if limit:
        lessons = lessons[:limit]
    report.total_lessons = len(lessons)

    prior = {} if force else store.sheet_hashes(engine=engine)

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

        sheet = generate_sheet_for_lesson(full, settings=settings, run_fn=run_fn)
        if sheet is None:
            # LLM seam degraded to nothing — do NOT write a sheet row so a later
            # real run reprocesses this lesson.
            report.skipped_no_llm += 1
            continue

        store.upsert_lesson_sheet(
            lesson_id=lesson_id,
            summary_md=sheet.summary_md,
            rules_md=sheet.rules_md,
            diagram_mermaid=sheet.diagram_mermaid,
            quiz=sheet.quiz,
            content_hash=chash,
            model=model,
            engine=engine,
        )

        report.processed += 1
        report.generated += 1

    return report
