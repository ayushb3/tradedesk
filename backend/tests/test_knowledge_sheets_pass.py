"""M8c PR-2 — study-sheet generation pass tests (issue #54).

Uses a temp-file SQLite engine and seeds fake `knowledge_lessons` rows via
`store.upsert_lesson`. A canned `run_fn` drives the claude-cli seam so the pass
runs deterministically with no real LLM. Also verifies the `llm=none` degradation
path, resumability, `--force`, and quiz sanitization.
"""
from __future__ import annotations

import json
import subprocess

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
    """run_fn returning --version success + a fixed sheet payload envelope."""
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


_GOOD_SHEET = {
    "summary_md": "This lesson covers the support and resistance framework and patience.",
    "rules_md": "- Wait for the level\n- Trade with the trend",
    "diagram_mermaid": "flowchart TD\n  A[Level] --> B[Trend] --> C[Entry]",
    "quiz": [
        {
            "q": "What is the first step?",
            "choices": ["Mark the level", "Enter immediately"],
            "answer_idx": 0,
            "explanation": "Levels come first.",
        },
        {
            "q": "When do you enter?",
            "choices": ["On the level with trend", "Never"],
            "answer_idx": 0,
            "explanation": "Trend alignment matters.",
        },
    ],
}


# --------------------------------------------------------------------------- #
# Upsert + get round-trip
# --------------------------------------------------------------------------- #

def test_sheet_upsert_and_get_round_trip(temp_engine):
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    store.upsert_lesson_sheet(
        lesson_id=1,
        summary_md="A summary.",
        rules_md="- rule one",
        diagram_mermaid="flowchart TD\n  A --> B",
        quiz=[{"q": "Q?", "choices": ["a", "b"], "answer_idx": 1, "explanation": "e"}],
        content_hash="hash1",
        model="claude-cli",
        engine=temp_engine,
    )

    row = store.get_lesson_sheet(1, engine=temp_engine)
    assert row is not None
    assert row["summary_md"] == "A summary."
    assert row["rules_md"] == "- rule one"
    assert row["diagram_mermaid"] == "flowchart TD\n  A --> B"
    assert row["quiz"] == [
        {"q": "Q?", "choices": ["a", "b"], "answer_idx": 1, "explanation": "e"}
    ]
    assert row["model"] == "claude-cli"
    assert row["generated_at"] is not None


def test_get_sheet_none_when_absent(temp_engine):
    _seed_lesson(temp_engine, 1, "No sheet here.")
    assert store.get_lesson_sheet(1, engine=temp_engine) is None


def test_upsert_sheet_is_idempotent(temp_engine):
    _seed_lesson(temp_engine, 1, "Lesson.")
    store.upsert_lesson_sheet(
        lesson_id=1, summary_md="v1", rules_md="", diagram_mermaid=None,
        quiz=[], content_hash="h", model="m", engine=temp_engine,
    )
    store.upsert_lesson_sheet(
        lesson_id=1, summary_md="v2", rules_md="", diagram_mermaid=None,
        quiz=[], content_hash="h", model="m", engine=temp_engine,
    )
    assert store.get_lesson_sheet(1, engine=temp_engine)["summary_md"] == "v2"
    assert store.sheet_hashes(engine=temp_engine) == {1: "h"}


# --------------------------------------------------------------------------- #
# Pass — generation + resumability + --force
# --------------------------------------------------------------------------- #

def test_pass_generates_sheet(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")

    report = run_sheet_generation(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(_GOOD_SHEET),
    )

    assert report.processed == 1
    assert report.generated == 1
    row = store.get_lesson_sheet(1, engine=temp_engine)
    assert row is not None
    assert row["summary_md"].startswith("This lesson")
    assert len(row["quiz"]) == 2


def test_resumability_skips_unchanged(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    run_fn = _canned_run_fn(_GOOD_SHEET)

    first = run_sheet_generation(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    assert first.processed == 1
    gen_at_1 = store.get_lesson_sheet(1, engine=temp_engine)["generated_at"]

    second = run_sheet_generation(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    assert second.processed == 0
    assert second.skipped_unchanged == 1
    assert second.generated == 0
    # No rewrite: generated_at unchanged.
    assert store.get_lesson_sheet(1, engine=temp_engine)["generated_at"] == gen_at_1


def test_force_regenerates(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    run_fn = _canned_run_fn(_GOOD_SHEET)

    run_sheet_generation(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    forced = run_sheet_generation(
        engine=temp_engine, settings=Settings(llm="claude-cli"), run_fn=run_fn,
        force=True,
    )
    assert forced.processed == 1
    assert forced.skipped_unchanged == 0
    assert forced.generated == 1


def test_limit_caps_lessons(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    for i in range(1, 4):
        _seed_lesson(temp_engine, i, f"Lesson {i} about the support and resistance framework.")

    report = run_sheet_generation(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(_GOOD_SHEET),
        limit=2,
    )
    assert report.total_lessons == 2
    assert report.processed == 2


# --------------------------------------------------------------------------- #
# llm=none degradation
# --------------------------------------------------------------------------- #

def test_llm_none_skips_and_writes_nothing(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _seed_lesson(temp_engine, 1, "support and resistance lesson.")
    _seed_lesson(temp_engine, 2, "Risk lesson.")

    report = run_sheet_generation(
        engine=temp_engine, settings=Settings(llm="none")
    )

    assert report.skipped_no_llm == 2
    assert report.processed == 0
    assert report.generated == 0
    assert store.get_lesson_sheet(1, engine=temp_engine) is None
    assert store.get_lesson_sheet(2, engine=temp_engine) is None
    assert store.sheet_hashes(engine=temp_engine) == {}


# --------------------------------------------------------------------------- #
# Quiz sanitization
# --------------------------------------------------------------------------- #

def test_quiz_sanitization_drops_bad_questions(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "Lesson.")

    payload = {
        "summary_md": "S",
        "rules_md": "- r",
        "diagram_mermaid": None,
        "quiz": [
            # valid
            {"q": "Good?", "choices": ["a", "b"], "answer_idx": 1, "explanation": "e"},
            # answer_idx out of range → dropped
            {"q": "Bad idx?", "choices": ["a", "b"], "answer_idx": 5, "explanation": "e"},
            # fewer than 2 choices → dropped
            {"q": "One choice?", "choices": ["only"], "answer_idx": 0, "explanation": "e"},
            # negative answer_idx → dropped
            {"q": "Neg?", "choices": ["a", "b"], "answer_idx": -1, "explanation": "e"},
        ],
    }

    run_sheet_generation(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(payload),
    )

    quiz = store.get_lesson_sheet(1, engine=temp_engine)["quiz"]
    assert len(quiz) == 1
    assert quiz[0]["q"] == "Good?"


def test_empty_diagram_string_coerced_to_none(temp_engine):
    from app.knowledge.sheets_pass import run_sheet_generation

    _reset_binary_cache()
    _seed_lesson(temp_engine, 1, "Lesson.")

    payload = {"summary_md": "S", "rules_md": "- r", "diagram_mermaid": "   ", "quiz": []}
    run_sheet_generation(
        engine=temp_engine,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(payload),
    )
    assert store.get_lesson_sheet(1, engine=temp_engine)["diagram_mermaid"] is None


# --------------------------------------------------------------------------- #
# generate_sheet_for_lesson unit behavior
# --------------------------------------------------------------------------- #

def test_generate_sheet_none_under_llm_none():
    from app.knowledge.generate import generate_sheet_for_lesson

    lesson = {"id": 1, "module": "7", "seq": 1, "title": "L", "raw_text": "body"}
    assert generate_sheet_for_lesson(lesson, settings=Settings(llm="none")) is None


def test_generate_sheet_coerces_missing_fields():
    from app.knowledge.generate import generate_sheet_for_lesson

    _reset_binary_cache()
    lesson = {"id": 1, "module": "7", "seq": 1, "title": "L", "raw_text": "body"}
    # Payload missing every field — parse model defaults everything.
    sheet = generate_sheet_for_lesson(
        lesson,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn({}),
    )
    assert sheet is not None
    assert sheet.summary_md == ""
    assert sheet.rules_md == ""
    assert sheet.diagram_mermaid is None
    assert sheet.quiz == []


def test_generate_sheet_shape_mismatch_returns_empty_sheet():
    from app.knowledge.generate import generate_sheet_for_lesson

    _reset_binary_cache()
    lesson = {"id": 1, "module": "7", "seq": 1, "title": "L", "raw_text": "body"}
    # quiz is not a list → validation error → empty StudySheet (not None).
    sheet = generate_sheet_for_lesson(
        lesson,
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn({"summary_md": "x", "quiz": "not-a-list"}),
    )
    assert sheet is not None
    assert sheet.summary_md == ""
    assert sheet.quiz == []
