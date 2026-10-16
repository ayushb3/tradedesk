"""M8c PR-1 — concept endpoint tests (issue #54).

Covers GET /knowledge/concepts, GET /knowledge/concepts/{slug}, and the non-breaking
enrichment of GET /knowledge/lessons/{id} (real concepts when present, still [] when
absent). Uses the isolated_db fixture so the routes read from a throwaway DB;
lessons + concepts are seeded via the store with the default (settings-derived)
engine.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.knowledge import store
from app.knowledge.chunking import chunk_text


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def _seed_lesson(lesson_id: int, module: str, seq: int, title: str,
                 raw_text: str = "transcript body"):
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


def _seed_concept_graph():
    """Two concepts, one lesson tagged with both, one link between them."""
    _seed_lesson(1, "7", 1, "Support Levels")
    store.upsert_concept(
        slug="support-resistance", name="Support and Resistance", category="Strategy",
        definition="The core framework.", aliases=["S/R"], seeded=True,
    )
    store.upsert_concept(
        slug="key-levels", name="Support Levels", category="Strategy",
        definition="Hourly S/R levels.", seeded=True,
    )
    store.replace_lesson_concepts(1, [
        {"slug": "support-resistance", "weight": 0.9},
        {"slug": "key-levels", "weight": 0.5},
    ])
    store.upsert_concept_links([
        {"a_slug": "support-resistance", "b_slug": "key-levels", "relation": "part_of"},
    ])


# --------------------------------------------------------------------------- #
# GET /concepts
# --------------------------------------------------------------------------- #

def test_concepts_empty_not_generated(client):
    r = client.get("/api/knowledge/concepts")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "not_generated"
    assert body["concept_count"] == 0
    assert body["concepts"] == []
    assert "make knowledge-concepts" in body["detail"]


def test_concepts_populated_grouped_sorted(client):
    _seed_concept_graph()
    r = client.get("/api/knowledge/concepts")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["status"] == "ok"
    assert body["concept_count"] == 2

    # Sorted by (category, name); both share category → name order.
    names = [c["name"] for c in body["concepts"]]
    assert names == sorted(names)

    by_slug = {c["slug"]: c for c in body["concepts"]}
    assert by_slug["support-resistance"]["lesson_count"] == 1
    assert by_slug["support-resistance"]["aliases"] == ["S/R"]


# --------------------------------------------------------------------------- #
# GET /concepts/{slug}
# --------------------------------------------------------------------------- #

def test_concept_detail_404(client):
    r = client.get("/api/knowledge/concepts/does-not-exist")
    assert r.status_code == 404


def test_concept_detail_populated(client):
    _seed_concept_graph()
    r = client.get("/api/knowledge/concepts/support-resistance")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "support-resistance"
    assert body["definition"] == "The core framework."
    assert body["lesson_count"] == 1

    assert len(body["lessons"]) == 1
    lesson = body["lessons"][0]
    assert lesson["lesson_id"] == 1
    assert lesson["weight"] == 0.9
    assert "Module 7" in lesson["citation"]

    assert len(body["related"]) == 1
    rel = body["related"][0]
    assert rel["slug"] == "key-levels"
    assert rel["relation"] == "part_of"


# --------------------------------------------------------------------------- #
# GET /lessons/{id} enrichment (non-breaking)
# --------------------------------------------------------------------------- #

def test_lesson_concepts_empty_when_none(client):
    _seed_lesson(5, "7", 1, "Untagged Lesson")
    r = client.get("/api/knowledge/lessons/5")
    assert r.status_code == 200
    assert r.json()["concepts"] == []


def test_lesson_concepts_present_when_tagged(client):
    _seed_concept_graph()
    r = client.get("/api/knowledge/lessons/1")
    assert r.status_code == 200
    concepts = r.json()["concepts"]
    assert len(concepts) == 2
    # Ordered by weight desc.
    assert concepts[0]["slug"] == "support-resistance"
    assert concepts[0]["weight"] == 0.9
    assert concepts[1]["slug"] == "key-levels"
    assert all({"slug", "name", "category", "weight"} <= set(c) for c in concepts)


# --------------------------------------------------------------------------- #
# GET /lessons/{id} study sheet (M8c PR-2, non-breaking)
# --------------------------------------------------------------------------- #

def test_lesson_sheet_null_when_absent(client):
    _seed_lesson(6, "7", 1, "Sheetless Lesson")
    r = client.get("/api/knowledge/lessons/6")
    assert r.status_code == 200
    assert r.json()["sheet"] is None


def test_lesson_sheet_populated_when_present(client):
    _seed_lesson(7, "7", 1, "Sheeted Lesson")
    store.upsert_lesson_sheet(
        lesson_id=7,
        summary_md="A short summary of the lesson.",
        rules_md="- rule one\n- rule two",
        diagram_mermaid="flowchart TD\n  A --> B",
        quiz=[
            {"q": "What?", "choices": ["a", "b"], "answer_idx": 1, "explanation": "e"},
        ],
        content_hash="hash7",
        model="claude-cli",
    )
    r = client.get("/api/knowledge/lessons/7")
    assert r.status_code == 200
    sheet = r.json()["sheet"]
    assert sheet is not None
    assert sheet["summary_md"] == "A short summary of the lesson."
    assert sheet["rules_md"] == "- rule one\n- rule two"
    assert sheet["diagram_mermaid"] == "flowchart TD\n  A --> B"
    assert sheet["model"] == "claude-cli"
    assert len(sheet["quiz"]) == 1
    q = sheet["quiz"][0]
    assert q["q"] == "What?"
    assert q["choices"] == ["a", "b"]
    assert q["answer_idx"] == 1
    assert q["explanation"] == "e"


def test_lesson_sheet_null_diagram(client):
    _seed_lesson(8, "7", 1, "No Diagram Lesson")
    store.upsert_lesson_sheet(
        lesson_id=8,
        summary_md="Summary.",
        rules_md="- r",
        diagram_mermaid=None,
        quiz=[],
        content_hash="hash8",
        model="claude-cli",
    )
    r = client.get("/api/knowledge/lessons/8")
    assert r.status_code == 200
    sheet = r.json()["sheet"]
    assert sheet is not None
    assert sheet["diagram_mermaid"] is None
    assert sheet["quiz"] == []
