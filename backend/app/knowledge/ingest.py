"""Lesson ingestion pipeline orchestration (PRD-M8 §4.1, R1–R5).

    build_mapping (parsing)  →  for each lesson:
        content-hash → skip if unchanged (R5)
        else: chunk (chunking) → embed (Embedder seam) → upsert vectors
              (vector_index seam) + upsert lesson row (store)

Pure orchestration; the CLI wrapper is ``backend/scripts/ingest_knowledge.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import Engine

from app.knowledge import store
from app.knowledge.chunking import chunk_text
from app.knowledge.embedder import Embedder, get_embedder
from app.knowledge.parsing import build_mapping
from app.knowledge.vector_index import VectorIndex, get_vector_index


@dataclass
class IngestReport:
    total_lessons: int = 0
    resolved: int = 0
    unresolved: list[int] = field(default_factory=list)
    ingested: int = 0          # newly processed (new or changed)
    skipped: int = 0           # unchanged (idempotent)
    chunks_written: int = 0
    module_counts: dict[str, int] = field(default_factory=dict)
    embedder_name: str = ""
    vector_backend: str = ""

    def summary(self) -> str:
        lines = [
            f"lessons:         {self.total_lessons}",
            f"resolved->module: {self.resolved}",
            f"unresolved:      {len(self.unresolved)} {self.unresolved or ''}".rstrip(),
            f"ingested (new/changed): {self.ingested}",
            f"skipped (unchanged):    {self.skipped}",
            f"chunks written:  {self.chunks_written}",
            f"embedder:        {self.embedder_name}",
            f"vector backend:  {self.vector_backend}",
            f"modules:         {dict(sorted(self.module_counts.items()))}",
        ]
        return "\n".join(lines)


def run_ingest(
    *,
    lessons_dir: Path | None = None,
    modules_dir: Path | None = None,
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
    force: bool = False,
    limit: int | None = None,
) -> IngestReport:
    """Ingest all lesson transcripts. Idempotent: unchanged lessons are skipped unless
    ``force``. ``limit`` caps how many lessons are processed (for smoke runs)."""
    tdir = lessons_dir or store.lessons_dir()
    mdir = modules_dir or store.modules_dir()
    embedder = embedder or get_embedder()
    index = index or get_vector_index(store.vector_dir())

    mapping = build_mapping(Path(tdir), Path(mdir))
    report = IngestReport(
        total_lessons=len(mapping.lessons) + len(mapping.unresolved),
        resolved=len(mapping.lessons),
        unresolved=mapping.unresolved,
        embedder_name=embedder.name,
        vector_backend=getattr(index, "backend", type(index).__name__),
    )

    prior = {} if force else store.existing_hashes(engine)
    lessons = mapping.lessons[:limit] if limit else mapping.lessons

    for lesson in lessons:
        chash = store.content_hash(lesson.raw_text)
        if not force and prior.get(lesson.lesson_id) == chash:
            report.skipped += 1
            continue

        # Re-processing an existing lesson: drop its old vectors first.
        index.delete_lesson(lesson.lesson_id)

        chunks = chunk_text(lesson.raw_text)
        if chunks:
            vectors = embedder.embed([c.text for c in chunks])
            ids = [f"{lesson.lesson_id}:{c.index}" for c in chunks]
            metas = [
                {
                    "lesson_id": lesson.lesson_id,
                    "module": lesson.module,
                    "seq": lesson.module_seq,
                    "title": lesson.title,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                }
                for c in chunks
            ]
            index.upsert(ids, vectors, [c.text for c in chunks], metas)

        store.upsert_lesson(
            lesson_id=lesson.lesson_id,
            module=lesson.module,
            seq=lesson.module_seq,
            title=lesson.title,
            source_path=str(lesson.source_path),
            raw_text=lesson.raw_text,
            chash=chash,
            embedder=embedder.name,
            chunk_count=len(chunks),
            engine=engine,
        )
        report.ingested += 1
        report.chunks_written += len(chunks)

    report.module_counts = store.module_counts(engine)
    return report
