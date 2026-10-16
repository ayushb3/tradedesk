"""Course Lesson Reader tests (M8b, issue #48).

Covers the three reader endpoints against an isolated temp DB (via the
``isolated_db`` fixture, which points the whole app at a throwaway SQLite file
and clears the cached engine). Lessons are seeded with ``store.upsert_lesson``
using the same default engine the routes resolve, so no real ingestion is
required. Everything runs in mock mode with no credentials.

Assertions target:
- Modules tree on ingested + not-ingested (empty table → available=False, no 500).
- Lesson fetch: chunk anchors, prev/next ids, sheet=null / concepts=[].
- Lesson 404 for a missing id.
- Progress upsert transitions (unread→read→starred→unread), updated_at changes,
  404 when setting progress on a non-existent lesson, 422 on invalid status.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.knowledge.chunking import chunk_text


@pytest.fixture()
def client(isolated_db):
    """FastAPI TestClient bound to the isolated temp DB."""
    from app.main import create_app

    return TestClient(create_app())


def _seed_lesson(
    lesson_id: int,
    module: str,
    seq: int,
    title: str,
    raw_text: str = "some transcript body here",
) -> None:
    """Insert a lesson row via the store, using the default (settings-derived)
    engine so it lands in the same DB the routes read from."""
    from app.knowledge import store

    store.upsert_lesson(
        lesson_id=lesson_id,
        module=module,
        seq=seq,
        title=title,
        source_path=f"/fake/{lesson_id}.txt",
        raw_text=raw_text,
        chash=store.content_hash(raw_text),
        embedder="hashing-v1",
        chunk_count=len(chunk_text(raw_text)),
    )


def _seed_corpus() -> None:
    # Deliberately out-of-order modules to exercise numeric-aware sorting.
    _seed_lesson(1, "1", 1, "Intro")
    _seed_lesson(2, "1", 2, "Confirmation Candles")
    _seed_lesson(3, "12.1", 1, "Advanced Setup")
    _seed_lesson(4, "3.2", 1, "Options Basics")
    _seed_lesson(5, "0", 1, "Welcome")


# --------------------------------------------------------------------------- #
# GET /modules
# --------------------------------------------------------------------------- #
def test_modules_not_ingested(client):
    """Empty knowledge_lessons → available=False, status=not_ingested, no 500."""
    resp = client.get("/api/knowledge/modules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["status"] == "not_ingested"
    assert body["detail"]
    assert body["lesson_count"] == 0
    assert body["module_count"] == 0
    assert body["modules"] == []


def test_modules_numeric_aware_order_and_counts(client):
    _seed_corpus()
    resp = client.get("/api/knowledge/modules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["status"] == "ok"
    assert body["lesson_count"] == 5
    assert body["module_count"] == 4

    # Numeric-aware: "0" < "1" < "3.2" < "12.1".
    order = [m["module"] for m in body["modules"]]
    assert order == ["0", "1", "3.2", "12.1"]

    mod1 = next(m for m in body["modules"] if m["module"] == "1")
    assert mod1["label"] == "Module 1"
    assert mod1["lesson_count"] == 2
    assert mod1["read_count"] == 0
    assert mod1["starred_count"] == 0
    # Lessons ordered by seq.
    assert [l["seq"] for l in mod1["lessons"]] == [1, 2]
    lesson = mod1["lessons"][0]
    assert lesson["status"] == "unread"
    assert lesson["citation"] == "Module 1 · Lesson 001 — Intro"


def test_modules_read_and_starred_counts(client):
    _seed_corpus()
    # read implies engaged; starred implies engaged too.
    client.put("/api/knowledge/lessons/1/progress", json={"status": "read"})
    client.put("/api/knowledge/lessons/2/progress", json={"status": "starred"})

    body = client.get("/api/knowledge/modules").json()
    mod1 = next(m for m in body["modules"] if m["module"] == "1")
    assert mod1["read_count"] == 2  # read + starred both count as engaged
    assert mod1["starred_count"] == 1
    statuses = {l["id"]: l["status"] for l in mod1["lessons"]}
    assert statuses == {1: "read", 2: "starred"}


# --------------------------------------------------------------------------- #
# GET /lessons/{id}
# --------------------------------------------------------------------------- #
def test_lesson_fetch_full_shape(client):
    body_text = " ".join(f"word{i}" for i in range(900))
    _seed_lesson(10, "7", 1, "Support Levels", raw_text=body_text)
    _seed_lesson(11, "7", 2, "Next Lesson")
    _seed_lesson(9, "7", 3, "Prev Lesson")

    resp = client.get("/api/knowledge/lessons/10")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert body["status"] == "ok"
    assert body["id"] == 10
    assert body["module"] == "7"
    assert body["seq"] == 1
    assert body["title"] == "Support Levels"
    assert body["citation"] == "Module 7 · Lesson 010 — Support Levels"
    assert body["raw_text"] == body_text
    assert body["sheet"] is None
    assert body["concepts"] == []

    # Chunk anchors mirror the ingest chunker and chunk_count.
    expected = chunk_text(body_text)
    assert body["chunk_count"] == len(expected)
    assert len(body["chunks"]) == len(expected)
    for anchor, c in zip(body["chunks"], expected):
        assert anchor["seq"] == c.index
        assert anchor["char_start"] == c.char_start
        assert anchor["char_end"] == c.char_end
        # offsets index back into the raw text
        assert body["raw_text"][anchor["char_start"]:anchor["char_end"]] == c.text

    # prev/next in global lesson-id order: 9, 10, 11 → prev=9, next=11.
    assert body["prev_lesson_id"] == 9
    assert body["next_lesson_id"] == 11

    # No progress row yet → unread / null.
    assert body["progress"]["status"] == "unread"
    assert body["progress"]["updated_at"] is None


def test_lesson_prev_next_at_ends(client):
    _seed_lesson(20, "1", 1, "First")
    _seed_lesson(21, "1", 2, "Last")

    first = client.get("/api/knowledge/lessons/20").json()
    assert first["prev_lesson_id"] is None
    assert first["next_lesson_id"] == 21

    last = client.get("/api/knowledge/lessons/21").json()
    assert last["prev_lesson_id"] == 20
    assert last["next_lesson_id"] is None


def test_lesson_reflects_progress(client):
    _seed_lesson(30, "1", 1, "With Progress")
    client.put("/api/knowledge/lessons/30/progress", json={"status": "starred"})

    body = client.get("/api/knowledge/lessons/30").json()
    assert body["progress"]["status"] == "starred"
    assert body["progress"]["updated_at"] is not None


def test_lesson_not_found_404(client):
    resp = client.get("/api/knowledge/lessons/999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Lesson 999 not found."


def test_lesson_not_found_on_empty_db(client):
    """Direct deep-link to a lesson on a not-ingested db → 404 (not availability)."""
    resp = client.get("/api/knowledge/lessons/1")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Lesson 1 not found."


# --------------------------------------------------------------------------- #
# PUT /lessons/{id}/progress
# --------------------------------------------------------------------------- #
def test_progress_upsert_transitions(client):
    _seed_lesson(40, "1", 1, "Toggle Me")

    # unread (default) → read
    r1 = client.put("/api/knowledge/lessons/40/progress", json={"status": "read"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1 == {
        "lesson_id": 40,
        "status": "read",
        "updated_at": b1["updated_at"],
    }
    assert b1["updated_at"] is not None
    t1 = b1["updated_at"]

    # read → starred, updated_at moves forward
    r2 = client.put("/api/knowledge/lessons/40/progress", json={"status": "starred"})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["status"] == "starred"
    assert b2["updated_at"] >= t1

    # starred → unread (valid state, keeps the row)
    r3 = client.put("/api/knowledge/lessons/40/progress", json={"status": "unread"})
    assert r3.status_code == 200
    assert r3.json()["status"] == "unread"

    # Round-trip via the lesson endpoint confirms the row persisted as unread.
    assert client.get("/api/knowledge/lessons/40").json()["progress"]["status"] == "unread"


def test_progress_updated_at_changes(client):
    _seed_lesson(41, "1", 1, "Timestamp")
    first = client.put("/api/knowledge/lessons/41/progress", json={"status": "read"}).json()
    second = client.put("/api/knowledge/lessons/41/progress", json={"status": "read"}).json()
    # Idempotent status, but updated_at is refreshed on every write.
    assert second["updated_at"] >= first["updated_at"]


def test_progress_404_for_missing_lesson(client):
    resp = client.put("/api/knowledge/lessons/777/progress", json={"status": "read"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Lesson 777 not found."

    # And no dangling progress row was created (modules would 500 or show it).
    _seed_lesson(1, "1", 1, "Real")
    body = client.get("/api/knowledge/modules").json()
    total_read = sum(m["read_count"] for m in body["modules"])
    assert total_read == 0


def test_progress_invalid_status_422(client):
    _seed_lesson(50, "1", 1, "Bad Status")
    resp = client.put("/api/knowledge/lessons/50/progress", json={"status": "bogus"})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Optional "open original lesson" link (TRADEDESK_LESSON_URL_TEMPLATE)
# --------------------------------------------------------------------------- #
def test_source_url_absent_by_default(client):
    _seed_lesson(1, "2", 1, "Intro")
    assert client.get("/api/knowledge/lessons/1").json()["source_url"] is None
    mod = client.get("/api/knowledge/modules").json()["modules"][0]
    assert mod["lessons"][0]["source_url"] is None


def test_source_url_built_from_template(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv(
        "TRADEDESK_LESSON_URL_TEMPLATE", "https://example.com/m/{module}/l/{lesson_id}"
    )
    get_settings.cache_clear()
    try:
        _seed_lesson(7, "2", 1, "Intro")
        assert (
            client.get("/api/knowledge/lessons/7").json()["source_url"]
            == "https://example.com/m/2/l/7"
        )
        mod = client.get("/api/knowledge/modules").json()["modules"][0]
        assert mod["lessons"][0]["source_url"] == "https://example.com/m/2/l/7"
    finally:
        get_settings.cache_clear()


def test_source_url_bad_template_degrades_to_none(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRADEDESK_LESSON_URL_TEMPLATE", "https://example.com/{nope}")
    get_settings.cache_clear()
    try:
        _seed_lesson(7, "2", 1, "Intro")
        assert client.get("/api/knowledge/lessons/7").json()["source_url"] is None
    finally:
        get_settings.cache_clear()
