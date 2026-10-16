"""M8c PR-1 — concept-extraction pass tests (issue #54).

Uses a temp-file SQLite engine and seeds a couple of fake `knowledge_lessons` rows via
`store.upsert_lesson`. A canned `run_fn` drives the claude-cli seam so the pass
runs deterministically with no real LLM. Also verifies the `llm=none` degradation
path and the drift guard.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app.config import Settings
from app.knowledge import store


def _wrap_in_cli_envelope(payload_json: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": payload_json,
    })


def _reset_binary_cache():
    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None


def _seed_lesson(engine, lesson_id: int, raw_text: str, title: str = "Lesson"):
    store.upsert_lesson(
        lesson_id=lesson_id,
        module="7",
        seq=lesson_id,
        title=title,
        source_path=f"/fake/{lesson_id}.txt",
        raw_text=raw_text,
        chash=store.content_hash(raw_text),
        embedder="hashing-v1",
        chunk_count=1,
        engine=engine,
    )


def _canned_run_fn(payload: dict):
    """run_fn returning --version success + a fixed concept payload envelope."""
    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(json.dumps(payload)),
            stderr="",
        )
    return _run


# --------------------------------------------------------------------------- #
# Seeding idempotency
# --------------------------------------------------------------------------- #

def test_seed_is_idempotent(temp_engine):
    from app.knowledge.seed import seed_concepts_into_db

    n1 = seed_concepts_into_db(engine=temp_engine)
    count1 = len(store.list_concepts(engine=temp_engine))
    n2 = seed_concepts_into_db(engine=temp_engine)
    count2 = len(store.list_concepts(engine=temp_engine))

    assert n1 == n2 == count1 == count2
    assert count1 > 0


# --------------------------------------------------------------------------- #
# Extraction + drift guard
# --------------------------------------------------------------------------- #

def test_extraction_writes_seed_concepts_and_discards_drift(temp_engine):
    from app.knowledge.concepts_pass import run_concept_extraction

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "A lesson about the support and resistance framework and patience.")

    # One valid seed slug, one bogus slug (must be discarded).
    payload = {
        "concepts": [
            {"slug": "support-resistance", "weight": 0.9},
            {"slug": "made-up-concept", "weight": 0.5},
        ],
        "links": [
            {"a_slug": "support-resistance", "b_slug": "patience", "relation": "related"},
            {"a_slug": "support-resistance", "b_slug": "bogus", "relation": "related"},
        ],
    }
    run_fn = _canned_run_fn(payload)

    report = run_concept_extraction(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=run_fn,
    )

    assert report.processed == 1
    assert report.concepts_written == 1  # only support-resistance, bogus discarded

    lesson_concepts = store.get_lesson_concepts(1, engine=temp_engine)
    slugs = {c["slug"] for c in lesson_concepts}
    assert slugs == {"support-resistance"}
    assert "made-up-concept" not in slugs

    # The drifted link (to bogus) was discarded; the valid one persisted.
    detail = store.get_concept_by_slug("support-resistance", engine=temp_engine)
    related_slugs = {r["slug"] for r in detail["related"]}
    assert "bogus" not in related_slugs


# --------------------------------------------------------------------------- #
# Resumability + --force
# --------------------------------------------------------------------------- #

def test_resumability_skips_unchanged(temp_engine):
    from app.knowledge.concepts_pass import run_concept_extraction

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    run_fn = _canned_run_fn({"concepts": [{"slug": "support-resistance", "weight": 1.0}], "links": []})

    first = run_concept_extraction(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    assert first.processed == 1
    assert first.skipped_unchanged == 0

    # Second run, unchanged content → skipped, no re-processing.
    second = run_concept_extraction(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    assert second.processed == 0
    assert second.skipped_unchanged == 1
    assert second.concepts_written == 0


def test_force_reprocesses(temp_engine):
    from app.knowledge.concepts_pass import run_concept_extraction

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    run_fn = _canned_run_fn({"concepts": [{"slug": "support-resistance", "weight": 1.0}], "links": []})

    run_concept_extraction(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    forced = run_concept_extraction(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn, force=True
    )
    assert forced.processed == 1
    assert forced.skipped_unchanged == 0


# --------------------------------------------------------------------------- #
# llm=none degradation
# --------------------------------------------------------------------------- #

def test_llm_none_skips_and_writes_nothing(temp_engine):
    from app.knowledge.concepts_pass import run_concept_extraction

    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    _seed_lesson(temp_engine, 2, "Risk lesson.")

    report = run_concept_extraction(
        engine=temp_engine, settings=Settings(llm="none")
    )

    assert report.skipped_no_llm == 2
    assert report.processed == 0
    assert report.concepts_written == 0
    # Seeding still happened (concepts exist) but no lesson has concepts.
    assert report.seeded_concepts > 0
    assert store.get_lesson_concepts(1, engine=temp_engine) == []
    assert store.get_lesson_concepts(2, engine=temp_engine) == []
    # No run markers written (so a later real run reprocesses).
    assert store.concept_run_hashes(engine=temp_engine) == {}


def test_limit_caps_lessons(temp_engine):
    from app.knowledge.concepts_pass import run_concept_extraction

    _reset_binary_cache()
    for i in range(1, 4):
        _seed_lesson(temp_engine, i, f"Lesson {i} about the support and resistance framework.")
    run_fn = _canned_run_fn({"concepts": [{"slug": "support-resistance", "weight": 1.0}], "links": []})

    report = run_concept_extraction(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=run_fn,
        limit=2,
    )
    assert report.total_lessons == 2
    assert report.processed == 2


def test_seed_defaults_to_bundled_example_and_env_override(tmp_path, monkeypatch):
    from app.knowledge.seed import load_seed

    monkeypatch.delenv("TRADEDESK_SEED_CONCEPTS", raising=False)
    default = load_seed()
    assert len(default.concepts) == 8
    assert "support-resistance" in default.slugs()

    custom = tmp_path / "mine.json"
    custom.write_text(
        '{"concepts": [{"slug": "x", "name": "X", "category": "Process"}],'
        ' "seed_links": []}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADEDESK_SEED_CONCEPTS", str(custom))
    assert load_seed().slugs() == {"x"}
