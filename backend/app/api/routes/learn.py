"""Learn MVP endpoints (prefix /api): today's drills, bars with a server-side no-future-leak
gate, deterministic grading, topic map, lesson list.

Unset TRADEDESK_LEARN_DIR -> every endpoint answers 200 {configured:false, message}.
The answer key, rubric and explanation never leave the server before an answer exists.
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import provider_dep, settings_dep
from app.config import Settings
from app.learn import lessons as ls
from app.learn import store
from app.learn.grading import Answers, grade
from app.models.market import Candle
from app.providers.base import DataProvider

router = APIRouter(tags=["learn"])


class AnswerIn(BaseModel):
    level: float | None = None
    enter: Literal["enter", "wait"]
    side: Literal["long", "short"] | None = None
    stop: float | None = None
    target: float | None = None
    note: str | None = None


def _root(settings: Settings):
    return store.learn_root(settings.learn_dir)


def _public(drill: store.Drill, notes, results: dict[str, dict]) -> dict:
    res = results.get(drill.id)
    score = None
    if res:
        try:
            score = int((res.get("grade") or {}).get("score"))
        except (TypeError, ValueError):
            score = None
    pub = store.DrillPublic(
        id=drill.id, set_date=drill.set_date, topic=drill.topic,
        lessons=ls.lesson_refs(notes, drill.lessons), ticker=drill.ticker, date=drill.date,
        timeframe=drill.timeframe, bars_from=drill.bars_from, pause_at=drill.pause_at,
        question_steps=drill.question_steps, review=drill.review,
        status="done" if res else "new", score=score,
    )
    return pub.model_dump(mode="json")


def _drill_or_404(root, drill_id: str) -> store.Drill:
    d = store.find_drill(root, drill_id)
    if d is None:
        raise HTTPException(status_code=404, detail="drill not found")
    return d


def _grade_result(drill: store.Drill, g: dict, notes) -> dict:
    return {
        "drill_id": drill.id,
        "score": g["score"],
        "steps": g["steps"],
        "answer_key": drill.answer_key.model_dump(mode="json"),
        "explanation": drill.explanation,
        "lessons": [x.model_dump(mode="json") for x in ls.lesson_refs(notes, drill.lessons)],
        "rule_refs": g["rule_refs"],
    }


@router.get("/learn/today")
def learn_today(settings: Settings = Depends(settings_dep)):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    today = store.today_pt().isoformat()
    dates = [d for d in store.list_set_dates(root) if d <= today]
    if not dates:
        return {"configured": True, "set_date": None, "drills": [], "review_pending": False}
    set_date = dates[-1]
    drills = store.load_set(root, set_date)
    notes = ls.open_course_notes(settings.db_path)
    results = store.latest_results(root)
    return {
        "configured": True,
        "set_date": set_date,
        "drills": [_public(d, notes, results) for d in drills],
        "review_pending": any(d.review for d in drills),
    }


@router.get("/learn/topics")
def learn_topics(settings: Settings = Depends(settings_dep)):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    notes = ls.open_course_notes(settings.db_path)
    drills = {d.id: d for d in store.all_drills(root)}
    per: dict[str, list[dict]] = {}
    for did, row in store.latest_results(root).items():
        topic = row.get("topic") or (drills[did].topic if did in drills else None)
        if topic:
            per.setdefault(topic, []).append(row)
    out, seen = [], set()

    def entry(slug: str, title: str) -> dict:
        rows = per.get(slug, [])
        scores = [r["grade"]["score"] for r in rows
                  if isinstance(r.get("grade"), dict) and isinstance(r["grade"].get("score"), (int, float))]
        dates = [d for d in (ls.pt_date(r.get("answered_at")) for r in rows) if d]
        return {"slug": slug, "title": title, "drills_done": len(rows),
                "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
                "last_date": max(dates) if dates else None}

    for t in ls.course_topics(notes):
        seen.add(t["slug"])
        out.append(entry(t["slug"], t["title"]))
    for slug in sorted(set(per) - seen):  # drilled topics the notes no longer list
        out.append(entry(slug, slug))
    return out


@router.get("/learn/lessons")
def learn_lessons(q: str = Query(""), settings: Settings = Depends(settings_dep)):
    if _root(settings) is None:
        return store.NOT_CONFIGURED
    return ls.list_lessons(ls.open_course_notes(settings.db_path), q)


@router.get("/learn/drills/{drill_id}")
def learn_drill(drill_id: str, settings: Settings = Depends(settings_dep)):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    drill = _drill_or_404(root, drill_id)
    return _public(drill, ls.open_course_notes(settings.db_path), store.latest_results(root))


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


@router.get("/learn/drills/{drill_id}/bars")
def learn_bars(
    drill_id: str,
    settings: Settings = Depends(settings_dep),
    provider: DataProvider = Depends(provider_dep),
):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    drill = _drill_or_404(root, drill_id)
    answered = drill_id in store.latest_results(root)
    start, pause, until = (_utc(drill.bars_from), _utc(drill.pause_at), _utc(drill.bars_until))
    try:
        day = date_cls.fromisoformat(drill.date)
        # The drill was built (and its answer key computed) from the cached session bars,
        # so serve those same bars. The provider is only a fallback: in mock mode it would
        # synthesize a different price path than the one the key was graded on.
        from app.learn.build import read_cached

        bars = read_cached(settings.db_path, drill.ticker, day) if drill.timeframe == "1m" else []
        if not bars:
            bars = provider.get_candles(drill.ticker, day, drill.timeframe)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"bars unavailable: {type(exc).__name__}")
    if answered:
        keep = [c for c in bars if start <= _utc(c.ts) <= until]
        revealed = until
    else:
        keep = [c for c in bars if start <= _utc(c.ts) < pause]  # the gate: ts < pause_at
        revealed = pause
    candles = [Candle.model_validate(c).model_dump(mode="json") for c in keep]
    return {"ticker": drill.ticker, "timeframe": drill.timeframe, "candles": candles,
            "revealed_until": revealed.isoformat().replace("+00:00", "Z")}


@router.post("/learn/drills/{drill_id}/answer")
def learn_answer(drill_id: str, body: AnswerIn, settings: Settings = Depends(settings_dep)):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    drill = _drill_or_404(root, drill_id)
    answers = Answers(**body.model_dump())
    g = grade(drill.answer_key, drill.grading_rubric, answers)
    store.append_result(root, {
        "drill_id": drill.id, "set_date": drill.set_date, "topic": drill.topic,
        "answered_at": store.now_utc_iso(), "answers": body.model_dump(), "grade": g,
    })
    return _grade_result(drill, g, ls.open_course_notes(settings.db_path))


@router.get("/learn/drills/{drill_id}/result")
def learn_result(drill_id: str, settings: Settings = Depends(settings_dep)):
    root = _root(settings)
    if root is None:
        return store.NOT_CONFIGURED
    drill = _drill_or_404(root, drill_id)
    row = store.latest_results(root).get(drill_id)
    if not row or not isinstance(row.get("grade"), dict):
        raise HTTPException(status_code=404, detail="no result yet")
    return _grade_result(drill, row["grade"], ls.open_course_notes(settings.db_path))
