"""M9a retrieval-composer tests (issue #58, R2).

`app.agents.retrieve.retrieve` composes course semantic search with lesson-concept
expansion. These tests:

- exercise the degradation paths through the HTTP route (`POST /api/knowledge/retrieve`):
  not-ingested and empty-query both return 200 available=False, empty hits — never 500.
- exercise the happy path by calling `retrieve()` directly with an injected
  hashing embedder + numpy vector index built over a tiny synthetic corpus
  (mirrors test_knowledge.py's ingest), then seed concepts on the hit lessons and
  assert concept expansion dedupes by slug (max weight) and sorts weight desc.

The hashing embedder + numpy index are dependency-free, so this is hermetic.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.knowledge import store
from app.knowledge.embedder import HashingEmbedder
from app.knowledge.ingest import run_ingest
from app.knowledge.vector_index import get_vector_index


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _force_hermetic_backends(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "hashing")
    monkeypatch.setenv("KNOWLEDGE_VECTOR_BACKEND", "numpy")


# --------------------------------------------------------------------------- #
# Synthetic corpus (mirrors test_knowledge.py) — padded so mid-text is distinctive.
# --------------------------------------------------------------------------- #

def _pad(tag: str) -> str:
    return " ".join(f"{tag} distinctive token number {i} here" for i in range(60))


_LESSON_BODIES = {
    1: "Welcome to the course. Today we cover confirmation candles and the support and resistance "
       "framework in detail. " + _pad("alpha"),
    2: "Support levels are the most important thing. We draw support levels every "
       "single day here. " + _pad("bravo"),
    3: "Risk management and position sizing using the fixed fraction keeps your "
       "account alive. " + _pad("charlie"),
}


def _write_corpus(root):
    tdir = root / "lessons"
    mdir = root / "modules"
    tdir.mkdir(parents=True)
    mdir.mkdir(parents=True)
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
        (tdir / name).write_text(header + _LESSON_BODIES[lid] + "\n", encoding="utf-8")
    parts = ["COURSE MODULE: 1", "=" * 20, ""]
    for lid in (1, 2, 3):
        parts += [
            f"LESSON: 1.{lid} Lesson {lid} - Video",
            f"TITLE: 1.{lid} Lesson {lid} - Video",
            f"URL: https://example.com/lesson-{lid}",
            "DATE: 2025-12-15",
            "-" * 20,
            _LESSON_BODIES[lid],
            "",
        ]
    (mdir / "Module_1.txt").write_text("\n".join(parts), encoding="utf-8")
    return tdir, mdir


def _ingest(tmp_path, engine, monkeypatch):
    """Ingest the synthetic corpus into `engine` + a numpy index; return the index."""
    tdir, mdir = _write_corpus(tmp_path / "src")
    vdir = tmp_path / "vec"
    monkeypatch.setattr(store, "vector_dir", lambda: vdir)
    index = get_vector_index(vdir)
    run_ingest(lessons_dir=tdir, modules_dir=mdir, engine=engine, index=index)
    return get_vector_index(vdir)


# --------------------------------------------------------------------------- #
# Endpoint degradation (never 500)
# --------------------------------------------------------------------------- #

def test_retrieve_not_ingested_returns_200_empty(client):
    r = client.post("/api/knowledge/retrieve", json={"query": "support levels", "k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "not_ingested"
    assert body["hits"] == []
    assert body["concepts"] == []


def test_retrieve_empty_query_degraded(client):
    r = client.post("/api/knowledge/retrieve", json={"query": "   ", "k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "empty_query"
    assert body["hits"] == []


def test_retrieve_runtime_error_degrades_not_500(isolated_db):
    """A runtime error inside retrieve() (e.g. model load failure, corrupt chroma
    dir) must degrade to 200 available=False status="unavailable", NEVER a 500.

    raise_server_exceptions=False so a would-be 500 surfaces as an HTTP 500
    response instead of propagating out of the TestClient."""
    from app.main import create_app

    test_client = TestClient(create_app(), raise_server_exceptions=False)
    with patch("app.agents.retrieve.retrieve", side_effect=RuntimeError("boom")):
        r = test_client.post("/api/knowledge/retrieve", json={"query": "support levels", "k": 5})

    assert r.status_code == 200, f"expected degraded 200, got {r.status_code}"
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "unavailable"
    assert body["hits"] == []


# --------------------------------------------------------------------------- #
# Happy path — retrieve() directly with injected engine/embedder/index
# --------------------------------------------------------------------------- #

def test_retrieve_returns_hits(tmp_path, temp_engine, monkeypatch):
    from app.agents.retrieve import retrieve

    index = _ingest(tmp_path, temp_engine, monkeypatch)
    resp = retrieve(
        "support levels", k=3,
        engine=temp_engine, embedder=HashingEmbedder(), index=index,
    )
    assert resp.available is True
    assert resp.status == "ok"
    assert resp.hits
    # The key-levels lesson (id 2) should be the top hit.
    assert resp.hits[0].lesson_id == 2


def test_concept_expansion_dedupes_max_weight_sorted(tmp_path, temp_engine, monkeypatch):
    """Two hit lessons both tagged 'support-resistance' at different weights → one concept
    entry at the MAX weight; results sorted by weight desc."""
    from app.agents.retrieve import retrieve

    index = _ingest(tmp_path, temp_engine, monkeypatch)

    # Concepts on the corpus lessons. support-resistance appears on lessons 1 (0.4) and
    # 2 (0.9) → must dedupe to a single entry at weight 0.9.
    store.upsert_concept(
        slug="support-resistance", name="Support and Resistance", category="Strategy",
        definition="Core framework.", engine=temp_engine,
    )
    store.upsert_concept(
        slug="key-levels", name="Support Levels", category="Strategy",
        definition="Hourly S/R.", engine=temp_engine,
    )
    store.upsert_concept(
        slug="risk", name="Risk", category="Risk", definition="Sizing.",
        engine=temp_engine,
    )
    store.replace_lesson_concepts(1, [{"slug": "support-resistance", "weight": 0.4}], engine=temp_engine)
    store.replace_lesson_concepts(2, [
        {"slug": "support-resistance", "weight": 0.9},
        {"slug": "key-levels", "weight": 0.6},
    ], engine=temp_engine)
    store.replace_lesson_concepts(3, [{"slug": "risk", "weight": 0.3}], engine=temp_engine)

    # k large enough to pull all three lessons into context.
    resp = retrieve(
        "support levels patience risk", k=8,
        engine=temp_engine, embedder=HashingEmbedder(), index=index,
    )
    assert resp.available is True

    slugs = [c.slug for c in resp.concepts]
    # Deduped by slug — support-resistance appears exactly once.
    assert slugs.count("support-resistance") == 1
    sr = next(c for c in resp.concepts if c.slug == "support-resistance")
    # Max weight across the two lessons kept (0.9, not 0.4).
    assert sr.weight == pytest.approx(0.9)

    # Sorted by weight desc (then name).
    weights = [c.weight for c in resp.concepts]
    assert weights == sorted(weights, reverse=True)


def test_expand_concepts_false_skips_expansion(tmp_path, temp_engine, monkeypatch):
    from app.agents.retrieve import retrieve

    index = _ingest(tmp_path, temp_engine, monkeypatch)
    store.upsert_concept(
        slug="support-resistance", name="Support and Resistance", category="Strategy",
        definition="x", engine=temp_engine,
    )
    store.replace_lesson_concepts(2, [{"slug": "support-resistance", "weight": 0.9}], engine=temp_engine)

    resp = retrieve(
        "support levels", k=3, expand_concepts=False,
        engine=temp_engine, embedder=HashingEmbedder(), index=index,
    )
    assert resp.available is True
    assert resp.hits
    assert resp.concepts == []
