"""knowledge base ingestion + search tests (M8a).

Hermetic by construction: forces the dependency-free `HashingEmbedder` and the
numpy vector index (env vars), builds a tiny synthetic corpus in a temp dir, and
runs against an isolated temp SQLite engine. No network, no torch, no chromadb —
so these pass whether or not the optional `requirements-knowledge.txt` extras are
installed.

Also included: a smoke of the real filename/module parser against the actual
corpus *iff* it is present on the machine (skipped in CI where it isn't).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.knowledge.chunking import chunk_text
from app.knowledge.embedder import HashingEmbedder, get_embedder
from app.knowledge.parsing import (
    build_mapping,
    clean_title,
    module_label_from_filename,
    parse_transcript_filename,
)
from app.knowledge.vector_index import NumpyVectorIndex, get_vector_index


@pytest.fixture(autouse=True)
def _force_hermetic_backends(monkeypatch):
    """Pin the no-deps embedder + numpy index for every test in this module."""
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "hashing")
    monkeypatch.setenv("KNOWLEDGE_VECTOR_BACKEND", "numpy")


# --------------------------------------------------------------------------- #
# Synthetic corpus
# --------------------------------------------------------------------------- #
# Bodies are padded to a few hundred words each (with a lesson-specific filler so
# every mid-text snippet is unique) so the content-matcher lands well past the
# scraper header, mirroring the real (long) transcripts.
def _pad(tag: str) -> str:
    return " ".join(f"{tag} distinctive token number {i} here" for i in range(60))


LESSON_BODIES = {
    1: "Welcome to the course. Today we cover confirmation candles and the support and resistance "
       "framework in detail. " + _pad("alpha"),
    2: "Support levels are the most important thing. We draw support levels every "
       "single day here. " + _pad("bravo"),
    3: "Risk management and position sizing using the fixed fraction keeps your "
       "account alive. " + _pad("charlie"),
}


def _write_corpus(root: Path) -> tuple[Path, Path]:
    tdir = root / "lessons"
    mdir = root / "modules"
    tdir.mkdir(parents=True)
    mdir.mkdir(parents=True)

    # Per-lesson transcripts (NNN_Title.txt) with the scraper header block.
    files = {
        1: "001_Welcome_to_the_Course_-____-__.txt",
        2: "002_Support_Levels_-____-__.txt",
        3: "003_Risk_Management_-____-__.txt",
    }
    for lid, name in files.items():
        header = (
            f"TITLE: Lesson {lid} - Video\n"
            f"URL: https://example.com/lesson-{lid}\n"
            "DATE: 2025-12-15\n"
            "--------------------\n"
        )
        (tdir / name).write_text(header + LESSON_BODIES[lid] + "\n", encoding="utf-8")

    # One module file concatenating all three lessons in order.
    parts = ["COURSE MODULE: 1", "=" * 20, ""]
    for lid in (1, 2, 3):
        parts += [
            f"LESSON: 1.{lid} Lesson {lid} - Video",
            f"TITLE: 1.{lid} Lesson {lid} - Video",
            f"URL: https://example.com/lesson-{lid}",
            "DATE: 2025-12-15",
            "-" * 20,
            LESSON_BODIES[lid],
            "",
        ]
    (mdir / "Module_1.txt").write_text("\n".join(parts), encoding="utf-8")
    return tdir, mdir


# --------------------------------------------------------------------------- #
# Unit: parsing / chunking / embedder
# --------------------------------------------------------------------------- #
def test_module_label_from_filename():
    assert module_label_from_filename("Module_7.txt") == "7"
    assert module_label_from_filename("Module_00_Extra.txt") == "0"
    assert module_label_from_filename("Module_12.1.txt") == "12.1"


def test_clean_title_strips_scraper_junk():
    raw = "12.1.3 Confirmation Candles Masterclass -        -   Video -  · -   PREREQUISITE"
    assert clean_title(raw) == "12.1.3 Confirmation Candles Masterclass"


def test_parse_transcript_filename(tmp_path):
    f = tmp_path / "061_3.2.3.3_IBKR_Options.txt"
    f.write_text("body", encoding="utf-8")
    tf = parse_transcript_filename(f)
    assert tf.lesson_id == 61
    assert "IBKR" in tf.raw_title


def test_chunking_overlap_and_offsets():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk_text(text, words_per_chunk=100, overlap_words=20)
    assert len(chunks) > 1
    # offsets index back into the original string
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.text
    # consecutive chunks overlap (start of next < end of prev)
    assert chunks[1].char_start < chunks[0].char_end
    assert chunks[0].index == 0 and chunks[1].index == 1


def test_chunking_empty_text():
    assert chunk_text("   ") == []


def test_hashing_embedder_normalized_and_deterministic():
    e = HashingEmbedder()
    v1 = e.embed(["support levels"])[0]
    v2 = e.embed(["support levels"])[0]
    assert v1 == v2  # deterministic
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert e.name == "hashing-v1"


def test_get_embedder_respects_env(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "hashing")
    assert get_embedder().name == "hashing-v1"


# --------------------------------------------------------------------------- #
# Unit: numpy vector index round-trip + idempotent upsert
# --------------------------------------------------------------------------- #
def test_numpy_index_roundtrip_and_persist(tmp_path):
    idx = NumpyVectorIndex(tmp_path / "vec")
    e = HashingEmbedder()
    texts = ["confirmation candles setup", "support levels breakout", "fixed fraction sizing"]
    vecs = e.embed(texts)
    idx.upsert(
        ids=["1:0", "1:1", "2:0"],
        vectors=vecs,
        texts=texts,
        metadatas=[
            {"lesson_id": 1, "module": "1", "seq": 1, "title": "a", "char_start": 0, "char_end": 1},
            {"lesson_id": 1, "module": "1", "seq": 1, "title": "a", "char_start": 2, "char_end": 3},
            {"lesson_id": 2, "module": "1", "seq": 2, "title": "b", "char_start": 0, "char_end": 1},
        ],
    )
    assert idx.count() == 3
    hits = idx.query(e.embed(["confirmation candles"])[0], k=2)
    assert hits and "confirmation" in hits[0].text

    # Persistence: a fresh index over the same dir sees the data.
    idx2 = NumpyVectorIndex(tmp_path / "vec")
    assert idx2.count() == 3

    # delete_lesson removes only that lesson's chunks.
    idx2.delete_lesson(1)
    assert idx2.count() == 1

    # Re-upserting the same ids is idempotent (no duplication).
    idx2.upsert(ids=["2:0"], vectors=[vecs[2]], texts=[texts[2]],
                metadatas=[{"lesson_id": 2, "module": "1", "seq": 2, "title": "b",
                            "char_start": 0, "char_end": 1}])
    assert idx2.count() == 1


# --------------------------------------------------------------------------- #
# Integration: full ingest + search over the synthetic corpus
# --------------------------------------------------------------------------- #
def test_full_ingest_and_search(tmp_path, temp_engine, monkeypatch):
    from app.knowledge import search as knowledge_search
    from app.knowledge import store
    from app.knowledge.ingest import run_ingest

    tdir, mdir = _write_corpus(tmp_path / "src")
    vdir = tmp_path / "vec"
    monkeypatch.setattr(store, "vector_dir", lambda: vdir)

    index = get_vector_index(vdir)
    report = run_ingest(
        lessons_dir=tdir, modules_dir=mdir, engine=temp_engine, index=index
    )
    assert report.resolved == 3
    assert report.unresolved == []
    assert report.ingested == 3
    assert report.chunks_written >= 3
    assert report.module_counts == {"1": 3}
    assert store.lesson_count(temp_engine) == 3

    # Search hits the right lesson with a citation.
    index2 = get_vector_index(vdir)
    resp = knowledge_search.search("support levels", k=3, engine=temp_engine, index=index2)
    assert resp.available is True
    assert resp.status == "ok"
    assert resp.results
    top = resp.results[0]
    assert top.lesson_id == 2  # the key-levels lesson
    assert top.module == "1"
    assert "Lesson 002" in top.citation
    # metadata offsets index back into the raw text is guaranteed by chunking test


def test_ingest_is_idempotent(tmp_path, temp_engine, monkeypatch):
    from app.knowledge import store
    from app.knowledge.ingest import run_ingest

    tdir, mdir = _write_corpus(tmp_path / "src")
    vdir = tmp_path / "vec"
    monkeypatch.setattr(store, "vector_dir", lambda: vdir)
    index = get_vector_index(vdir)

    r1 = run_ingest(lessons_dir=tdir, modules_dir=mdir, engine=temp_engine, index=index)
    assert r1.ingested == 3 and r1.skipped == 0

    # Second run: nothing changed → all skipped, no new chunks.
    index2 = get_vector_index(vdir)
    r2 = run_ingest(lessons_dir=tdir, modules_dir=mdir, engine=temp_engine, index=index2)
    assert r2.ingested == 0
    assert r2.skipped == 3


def test_search_not_ingested_state(temp_engine):
    """Empty DB → available=False with a clear 'not ingested' status (no raise)."""
    from app.knowledge import search as knowledge_search

    # lesson_count==0 short-circuits before the index is constructed.
    resp = knowledge_search.search("anything", k=5, engine=temp_engine)
    assert resp.available is False
    assert resp.status == "not_ingested"


def test_search_empty_query(temp_engine):
    from app.knowledge import search as knowledge_search

    resp = knowledge_search.search("   ", k=5, engine=temp_engine)
    assert resp.available is False
    assert resp.status == "empty_query"


# --------------------------------------------------------------------------- #
# Optional smoke against the REAL corpus (skipped when not present)
# --------------------------------------------------------------------------- #
def _real_dirs():
    from app.knowledge import store

    return store.lessons_dir(), store.modules_dir()


@pytest.mark.skipif(
    not os.getenv("TRADEDESK_LESSONS_DIR") or not _real_dirs()[0].exists(),
    reason="TRADEDESK_LESSONS_DIR not set / corpus not on this machine",
)
def test_real_corpus_maps_all_lessons():
    tdir, mdir = _real_dirs()
    result = build_mapping(tdir, mdir)
    assert len(result.lessons) > 0
    assert result.unresolved == []
    # every lesson resolved to a module
    assert all(l.module for l in result.lessons)
