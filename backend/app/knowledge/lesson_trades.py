"""Your trades tagged with a lesson's setup: links a lesson to your own setup tags.

A setup tag (``tags`` / ``trade_tags``, e.g. "VWAP-reclaim") belongs to a lesson when every
word of the tag name shows up in the lesson's title or in the names of the concepts the lesson
teaches. A few trading abbreviations expand (PDH = prior day high, OR = opening range, ...).
Matching is deterministic and reads no lesson text beyond its title and concept names; only
trade ids, dates, symbols and tag names leave this module.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import knowledge_lessons
from app.db.schema import tags as tags_t
from app.db.schema import trade_tags as trade_tags_t
from app.db.schema import trades as trades_t

MAX_TRADES = 50
_WORD = re.compile(r"[a-z0-9]+")
# Each abbreviation stands for these phrases (the bare short form counts too, except "or").
ALIASES: dict[str, tuple[str, ...]] = {
    "or": ("opening range", "orb"),
    "orb": ("orb", "opening range"),
    "pdh": ("pdh", "prior day high", "previous day high"),
    "pdl": ("pdl", "prior day low", "previous day low"),
    "pmh": ("pmh", "premarket high", "pre market high"),
    "pml": ("pml", "premarket low", "pre market low"),
    "hod": ("hod", "high of day"),
    "lod": ("lod", "low of day"),
    "ema": ("ema", "moving average"),
}


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _has_phrase(words: list[str], phrase: str) -> bool:
    """``phrase`` occurs as consecutive words; a word of 4+ letters also matches longer forms
    (break -> breaks, breakout)."""
    pw = _words(phrase)
    if not pw:
        return False
    for i in range(len(words) - len(pw) + 1):
        if all(words[i + k] == w or (len(w) >= 4 and words[i + k].startswith(w))
               for k, w in enumerate(pw)):
            return True
    return False


def tag_matches(tag_name: str, text: str) -> bool:
    words = _words(text)
    tokens = _words(tag_name)
    return bool(tokens) and all(
        any(_has_phrase(words, p) for p in ALIASES.get(t, (t,))) for t in tokens
    )


def matching_tags(tag_names: list[str], title: str, concept_names: list[str]) -> list[str]:
    text = " . ".join([title or "", *concept_names])
    return [n for n in tag_names if tag_matches(n, text)]


def trades_for_lesson(lesson_id: int, engine: Engine | None = None) -> dict | None:
    """{lesson_id, tags, trades:[{trade_id, session_date, symbol, label, tags}]} newest first,
    or None when the lesson does not exist."""
    from app.knowledge import store

    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        title = conn.execute(
            select(knowledge_lessons.c.title).where(knowledge_lessons.c.id == lesson_id)
        ).scalar()
        if title is None:
            return None
        tag_rows = conn.execute(select(tags_t.c.id, tags_t.c.name).order_by(tags_t.c.name)).all()
    concepts = [c["name"] for c in store.get_lesson_concepts(lesson_id, engine)]
    names = matching_tags([r[1] for r in tag_rows], title, concepts)
    ids = {r[0]: r[1] for r in tag_rows if r[1] in names}
    if not ids:
        return {"lesson_id": lesson_id, "tags": [], "trades": []}
    with engine.connect() as conn:
        rows = conn.execute(
            select(trades_t.c.id, trades_t.c.session_date, trades_t.c.symbol,
                   trades_t.c.contract_label, trade_tags_t.c.tag_id)
            .select_from(trades_t.join(trade_tags_t, trade_tags_t.c.trade_id == trades_t.c.id))
            .where(trade_tags_t.c.tag_id.in_(list(ids)))
            .where(trades_t.c.simulated.is_(False))
            .where(trades_t.c.context != "training")
            .order_by(trades_t.c.open_ts_utc.desc(), trades_t.c.id.desc())
        ).all()
    by_trade: dict[int, dict] = {}
    for tid, day, symbol, label, tag_id in rows:
        t = by_trade.setdefault(tid, {
            "trade_id": tid,
            "session_date": day.isoformat() if isinstance(day, date) else str(day),
            "symbol": symbol, "label": label, "tags": [],
        })
        if ids[tag_id] not in t["tags"]:
            t["tags"].append(ids[tag_id])
    return {"lesson_id": lesson_id, "tags": names, "trades": list(by_trade.values())[:MAX_TRADES]}
