"""Lesson / topic enrichment for Learn, read through app.knowledge.course_search.

Everything degrades to nulls / empty lists when the course notes are unset, missing or broken;
nothing here raises.
"""
from __future__ import annotations

import re
from collections import defaultdict
from zoneinfo import ZoneInfo

from app.knowledge import course_index as ci
from app.knowledge import course_search as cs

from .store import LessonRef

# Topic order for the map: these first (when present), then the rest alphabetically.
TOPIC_FIRST = ("levels", "patience-candles", "stops", "trade-management")
PT = ZoneInfo("America/Los_Angeles")


def open_course_notes(db_path: str | None = None):
    """CourseNotes or None. Never raises."""
    try:
        if db_path is None:
            from app.config import get_settings

            db_path = get_settings().db_path
        notes, _err = cs.open_notes(db_path)
        return notes
    except Exception:
        return None


def _nat_key(lesson_id: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-]", lesson_id)]


def lesson_ref(notes, lesson_id: str) -> LessonRef:
    if notes is None:
        return LessonRef(id=lesson_id)
    try:
        sheet = notes.get_sheet(lesson_id)
    except Exception:
        return LessonRef(id=lesson_id)
    if not sheet.get("found"):
        return LessonRef(id=lesson_id)
    return LessonRef(id=lesson_id, title=sheet.get("title"), video_url=sheet.get("video"))


def lesson_refs(notes, ids: list[str]) -> list[LessonRef]:
    return [lesson_ref(notes, i) for i in ids]


def _topic_title(notes, slug: str) -> str:
    try:
        t = notes.get_topic(slug)
        return t.get("title") or slug
    except Exception:
        return slug


def course_topics(notes) -> list[dict]:
    """[{slug, title}] in map order. [] when notes are unavailable."""
    if notes is None:
        return []
    try:
        slugs = notes.topic_slugs()
    except Exception:
        return []
    first = [s for s in TOPIC_FIRST if s in slugs]
    rest = sorted(s for s in slugs if s not in first)
    return [{"slug": s, "title": _topic_title(notes, s)} for s in first + rest]


def _lesson_topics(notes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    try:
        for slug in notes.topic_slugs():
            p = notes._topic_path(slug)
            if not p:
                continue
            for e in ci.topic_lessons(p.read_text(encoding="utf-8", errors="replace")):
                if slug not in out[e["lesson"]]:
                    out[e["lesson"]].append(slug)
    except Exception:
        pass
    return out


def _all_sheets(notes) -> list[dict]:
    seen: dict[str, dict] = {}
    for c in notes.ready().index.chunks:
        if c["kind"] == "sheet" and c["lesson"] not in seen:
            seen[c["lesson"]] = c
    return sorted(seen.values(), key=lambda c: (_nat_key(c["module"] or ""), _nat_key(c["lesson"])))


def _row(c: dict, topics: dict[str, list[str]]) -> dict:
    return {"id": c["lesson"], "title": c.get("title"), "module": c.get("module"),
            "video_url": c.get("video"), "topics": topics.get(c["lesson"], [])}


def list_lessons(notes, q: str = "") -> list[dict]:
    """All lessons in module order, or the lessons a course search for ``q`` surfaces."""
    if notes is None:
        return []
    try:
        topics = _lesson_topics(notes)
        sheets = _all_sheets(notes)
        by_id = {c["lesson"]: c for c in sheets}
        q = (q or "").strip()
        if not q:
            return [_row(c, topics) for c in sheets]
        res = notes.search(q, k=50)
        ids: list[str] = []
        for item in list(res.get("lessons", [])) + list(res.get("claims", [])):
            lid = item.get("lesson")
            if lid and lid in by_id and lid not in ids:
                ids.append(lid)
        return [_row(by_id[i], topics) for i in ids]
    except Exception:
        return []


def pt_date(iso: str | None) -> str | None:
    """YYYY-MM-DD in Pacific time for a UTC ISO timestamp."""
    if not iso:
        return None
    from datetime import datetime, timezone

    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(PT).date().isoformat()
