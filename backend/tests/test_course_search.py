"""Course-notes search: chunking, cache rebuilds, ranking and the MCP tools.

Fixtures are synthetic (tests/fixtures/course_notes); no real notes are read.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.desk_mcp import mock, tools
from app.desk_mcp.server import create_server
from app.knowledge import course_index as ci
from app.knowledge import course_search as cs
from app.knowledge.embedder import HashingEmbedder

FIXTURES = Path(__file__).parent / "fixtures" / "course_notes"
QUERY = "how to hold runners on a small account"


@pytest.fixture()
def notes_dir(tmp_path):
    dst = tmp_path / "notes"
    shutil.copytree(FIXTURES, dst)
    return dst


@pytest.fixture()
def kb(notes_dir, tmp_path):
    return cs.CourseNotes(notes_dir, tmp_path / "cache" / ci.CACHE_NAME, HashingEmbedder())


# ------------------------------------------------------------------- chunking

def test_topic_chunks_split_at_h2_and_h3_and_skip_related():
    raw = (FIXTURES / "topics" / "trade-management.md").read_text(encoding="utf-8")
    chunks = ci.chunk_topic("topics/trade-management.md", raw)
    sections = [c.section for c in chunks]
    assert sections == [
        "The answer", "How it works > Trimming", "How it works > Trailing a runner",
        "Where lessons disagree", "Small-account and 0DTE notes",
        "What the course doesn't cover",
    ]
    assert all(c.kind == "topic" and c.slug == "trade-management" for c in chunks)
    assert all(c.source == "topics/trade-management.md" for c in chunks)
    assert "type: reference" not in chunks[0].text  # frontmatter stripped


def test_sheet_chunks_split_at_h2_with_video_and_module():
    raw = (FIXTURES / "module-2" / "lesson-2.1.md").read_text(encoding="utf-8")
    chunks = ci.chunk_sheet("module-2/lesson-2.1.md", raw)
    assert [c.section for c in chunks] == [
        "In one paragraph", "Key ideas", "Setup checklist", "Common mistakes"]  # quiz skipped
    c = chunks[0]
    assert (c.kind, c.lesson, c.module) == ("sheet", "2.1", "2")
    assert c.video == "https://example.com/video/3"
    assert c.title.startswith("2.1 ")


def test_dotted_lesson_ids_and_module_dirs():
    assert ci.lesson_id_from_path("module-3.1/lesson-3.1.1.md") == "3.1.1"
    assert ci.module_from_path("module-3.1/lesson-3.1.1.md") == "3.1"


def test_each_claim_line_is_one_chunk_inheriting_video(notes_dir):
    man = ci.scan_sources(notes_dir)
    claims = [c for c in ci.collect_chunks(notes_dir, man) if c.kind == "claim"]
    assert len(claims) == 5
    c = next(c for c in claims if c.lesson == "2.3")
    assert c.slug == "position-sizing" and c.section == "formula"
    assert c.module == "2" and c.video == "https://example.com/video/4"
    assert c.extra["tags"] == ["position-sizing"]


def test_root_files_are_skipped(notes_dir):
    assert not any(p.endswith("index.md") for p in ci.scan_sources(notes_dir))


def test_bad_claim_lines_are_ignored():
    assert ci.chunk_claims("claims/x.jsonl", 'not json\n{"claim": ""}\n[1]\n') == []


# --------------------------------------------------------------- cache rebuilds

def test_builds_once_then_reuses_cache_when_unchanged(kb):
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is True
    built = ci.read_meta(kb.cache)[0]["built_at"]
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is False
    assert ci.read_meta(kb.cache)[0]["built_at"] == built


def test_rebuilds_on_content_change(kb, notes_dir):
    ci.ensure_index(kb.root, kb.cache, kb.embedder)
    p = notes_dir / "topics" / "stops.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "## The answer\n", "## The answer\nExtra zebra sentence. ", 1), encoding="utf-8")
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is True
    assert any("zebra" in c["text"] for c in ci.load_index(kb.cache).chunks)


def test_rebuilds_on_mtime_only_change(kb, notes_dir):
    ci.ensure_index(kb.root, kb.cache, kb.embedder)
    p = notes_dir / "topics" / "stops.md"
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is True


def test_rebuilds_when_file_set_changes(kb, notes_dir):
    ci.ensure_index(kb.root, kb.cache, kb.embedder)
    (notes_dir / "topics" / "stops.md").unlink()
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is True
    assert "stops" not in kb.status()["topics"]
    (notes_dir / "topics" / "new-topic.md").write_text("# New\n\n## The answer\nHi there.\n")
    assert ci.ensure_index(kb.root, kb.cache, kb.embedder) is True
    assert "new-topic" in kb.status()["topics"]


def test_rebuild_reuses_vectors_for_unchanged_chunks(kb, notes_dir):
    first = ci.build_index(kb.root, kb.cache, kb.embedder)
    assert first["embedded"] == first["chunks"]
    p = notes_dir / "topics" / "stops.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n## Extra\nBrand new section.\n", encoding="utf-8")
    second = ci.build_index(kb.root, kb.cache, kb.embedder)
    assert second["embedded"] == 1


def test_search_after_edit_sees_new_text(kb, notes_dir):
    kb.search("stop")
    p = notes_dir / "topics" / "stops.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n## Quokka rule\nAlways quokka.\n", encoding="utf-8")
    top = kb.search("quokka")["topics"][0]
    assert top["slug"] == "stops" and top["section"] == "Quokka rule"


# --------------------------------------------------------------------- ranking

def test_acceptance_query_ranks_trade_management_first(kb):
    res = kb.search(QUERY)
    assert res["topics"][0]["slug"] == "trade-management"
    top_slugs = [t["slug"] for t in res["topics"]]
    assert "stops" in top_slugs and "position-sizing" in top_slugs
    assert res["topic_order"][0] == "trade-management"


def test_group_order_topics_then_lessons_then_claims(kb):
    res = kb.search(QUERY, k=8)
    assert list(res)[-3:] == ["topics", "lessons", "claims"]
    assert res["topics"] and res["lessons"] and res["claims"]
    assert len(res["topics"]) + len(res["lessons"]) + len(res["claims"]) <= 8
    assert {t["kind"] for t in res["topics"]} == {"topic"}
    assert {t["kind"] for t in res["lessons"]} == {"sheet"}
    assert {t["kind"] for t in res["claims"]} == {"claim"}


def test_result_items_carry_citation_fields(kb):
    res = kb.search("trail the runner behind higher lows")
    lesson = res["lessons"][0]
    assert lesson["lesson"] == "2.1" and lesson["video"] == "https://example.com/video/3"
    assert lesson["section"] and lesson["snippet"] and lesson["source"].startswith("module-")
    assert "[[" not in json.dumps(res)  # wikilinks are flattened


def test_topic_chunks_are_boosted_over_equal_lesson_text(kb, monkeypatch):
    query = "dollar risk stop distance"
    boosted = kb.search(query, k=8)
    assert boosted["topics"][0]["slug"] == "position-sizing"
    monkeypatch.setattr(cs, "TOPIC_BOOST", 1.0)
    flat = kb.search(query, k=8)
    assert boosted["topics"][0]["score"] > flat["topics"][0]["score"]


def test_k_limits_total_and_stopword_query_is_safe(kb):
    res = kb.search(QUERY, k=3)
    assert len(res["topics"]) + len(res["lessons"]) + len(res["claims"]) <= 3
    assert kb.search("the of and")["topics"] == []


# ---------------------------------------------------------------- direct reads

def test_get_topic_get_sheet_and_lessons_for(kb):
    t = kb.get_topic("stops")
    assert t["found"] and t["note"].startswith("# Protective stops") and "[[" not in t["note"]
    assert kb.get_topic("nope")["found"] is False
    assert kb.get_topic("../x")["found"] is False
    s = kb.get_sheet("3.1.1")
    assert s["found"] and s["video"] == "https://example.com/video/5" and s["module"] == "3.1"
    assert kb.get_sheet("9.9")["found"] is False and kb.get_sheet("../..")["found"] is False
    lf = kb.lessons_for("trade-management")
    assert [x["lesson"] for x in lf["lessons"]] == ["1.2", "2.1", "3.1.1"]
    assert lf["lessons"][0]["video"] == "https://example.com/video/2"
    assert lf["lessons"][1]["cited"] >= 2
    assert kb.lessons_for("Position Sizing")["topic"] == "position-sizing"


def test_status_counts(kb):
    st = kb.status()
    assert st["counts"]["topic"] > 0
    assert st["counts"]["sheet"] == 5 * 4 and st["counts"]["claim"] == 5
    assert st["embedder"] == "hashing-v1" and st["last_build"].endswith("Z")


# ------------------------------------------------------ unconfigured + the tools

@pytest.fixture()
def ctx(tmp_path):
    from app.db.connection import get_engine

    path = tmp_path / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


def _set_dir(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(cs.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(cs.ENV_VAR, str(value))
    get_settings.cache_clear()


@pytest.mark.parametrize("which", ["unset", "missing"])
def test_unconfigured_tools_never_raise(ctx, monkeypatch, tmp_path, which):
    _set_dir(monkeypatch, None if which == "unset" else tmp_path / "nope")
    calls = [lambda: tools.search_course(ctx, "x"), lambda: tools.get_topic(ctx, "a"),
             lambda: tools.get_sheet(ctx, "1.1"), lambda: tools.lessons_for(ctx, "a"),
             lambda: tools.course_status(ctx)]
    for call in calls:
        out = call()
        assert out["configured"] is False and "course notes not configured" in out["message"]
    assert tools.course_status(ctx)["counts"] == {}
    assert not (Path(ctx.db_path).parent / ci.CACHE_NAME).exists()


def test_tools_work_and_cache_sits_next_to_db(ctx, monkeypatch, notes_dir):
    _set_dir(monkeypatch, notes_dir)
    res = tools.search_course(ctx, QUERY, 8)
    assert res["topics"][0]["slug"] == "trade-management"
    assert (Path(ctx.db_path).parent / ci.CACHE_NAME).exists()
    st = tools.course_status(ctx)
    assert st["configured"] and st["counts"]["total"] > 0
    assert tools.get_sheet(ctx, "1.2")["found"]


def test_broken_notes_dir_returns_error_payload(ctx, monkeypatch, tmp_path):
    bad = tmp_path / "n"
    (bad / "topics").mkdir(parents=True)
    (bad / "topics" / "a.md").write_text("# A\n\n## The answer\ntext\n")
    _set_dir(monkeypatch, bad)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ci, "build_index", boom)
    out = tools.search_course(ctx, "text")
    assert "error" in out and "boom" in out["error"]


def test_mcp_server_exposes_read_only_course_tools(ctx, monkeypatch, notes_dir):
    _set_dir(monkeypatch, notes_dir)
    server = create_server(ctx)

    async def run():
        listed = await server.list_tools()
        res = await server.call_tool("search_course", {"query": QUERY, "k": 6})
        status = await server.call_tool("course_status", {})
        return listed, res, status

    listed, res, status = asyncio.run(run())
    by_name = {t.name: t for t in listed}
    names = {"search_course", "get_topic", "get_sheet", "lessons_for", "course_status"}
    assert names <= set(by_name)
    for n in names:
        assert by_name[n].annotations.readOnlyHint is True
    for n in ("search_course", "get_topic"):
        d = by_name[n].description
        assert "rules" in d and "get_day" in d and "video" in d and "not cover" in d

    def payload(r):
        content = r[0] if isinstance(r, tuple) else r
        return json.loads(content[0].text)

    assert payload(res)["topics"][0]["slug"] == "trade-management"
    assert payload(status)["counts"]["sheet"] == 20


def test_course_modules_have_no_network_imports():
    from tests.test_desk_mcp import scan_source

    base = Path(__file__).resolve().parents[1] / "app" / "knowledge"
    for name in ("course_index.py", "course_search.py"):
        assert scan_source((base / name).read_text(encoding="utf-8"), name) == []


def test_related_section_links_become_topic_neighbours():
    raw = (FIXTURES / "topics" / "trade-management.md").read_text(encoding="utf-8")
    chunks = ci.chunk_topic("topics/trade-management.md", raw)
    assert chunks[0].extra["related"] == ["stops", "position-sizing"]
