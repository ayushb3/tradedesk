"""Pattern -> lesson map: which local lessons address a flagged pattern.

The map lives in ``rules.lessons`` (personal ``data/rules.yaml``). Only lesson ids
and the user's own one-line reasons ever leave this module: no lesson title, text
or excerpt is read or returned. A link resolves when the id exists in the local
knowledge base; the href is the local reader route.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import knowledge_lessons
from app.desk.rules import Rules

# Ledger tag codes (see ``ledger.Tag``) plus the two day-level patterns the desk
# derives from plans and the window.
PATTERN_KEYS: frozenset[str] = frozenset({
    "added_lower", "added_higher", "scaled_in", "held_loser", "gave_back",
    "runner_paid", "blown_max_loss", "stop_risk", "overnight", "still_open",
    "late_fill", "off_plan",
    # Market-data patterns (#52): need option bars, so they appear only when measured.
    "held_past_stop", "exited_early",
})
# Friendlier spellings accepted in the rules file; normalised to the codes above.
PATTERN_ALIASES: dict[str, str] = {
    "stop_at_risk": "stop_risk",
    "held_overnight": "overnight",
}
ALLOWED_PATTERNS: frozenset[str] = PATTERN_KEYS | frozenset(PATTERN_ALIASES)


def canonical(pattern: str) -> str:
    return PATTERN_ALIASES.get(pattern, pattern)


def is_known_pattern(pattern: str) -> bool:
    """Pattern keys, aliases, and ``rule_<n>`` (rule numbers are checked by validate)."""
    return pattern in ALLOWED_PATTERNS or (
        pattern.startswith("rule_") and pattern[5:].isdigit()
    )


def href_for(lesson_pk: int) -> str:
    return f"/knowledge/lessons/{lesson_pk}"


def _resolve(conn, lesson_id: str) -> int | None:
    """Primary key for a plain numeric id or a ``module.seq`` id (e.g. ``12.1.3``)."""
    raw = str(lesson_id).strip()
    if raw.isdigit():
        return conn.execute(
            select(knowledge_lessons.c.id).where(knowledge_lessons.c.id == int(raw))
        ).scalar()
    module, _, seq = raw.rpartition(".")
    if module and seq.isdigit():
        return conn.execute(
            select(knowledge_lessons.c.id).where(and_(
                knowledge_lessons.c.module == module, knowledge_lessons.c.seq == int(seq),
            ))
        ).scalar()
    return None


def corpus_ingested(engine: Engine | None = None) -> bool:
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        return conn.execute(select(knowledge_lessons.c.id).limit(1)).first() is not None


def _counts(tag_codes: Iterable[str] | Mapping[str, int]) -> Counter:
    if isinstance(tag_codes, Mapping):
        return Counter({canonical(k): int(v) for k, v in tag_codes.items() if v})
    return Counter(canonical(c) for c in tag_codes)


def lessons_for(
    tag_codes: Iterable[str] | Mapping[str, int], rules: Rules, engine: Engine | None = None,
) -> list[dict[str, Any]]:
    """Deduped lesson links for these tag codes, most-common pattern first.

    ``tag_codes`` is a list with one entry per position carrying the tag (or a
    ``{code: count}`` mapping). Each item is
    ``{pattern, lesson_id, reason, href, available}``; ``href`` is None and
    ``available`` False when the id does not resolve (or nothing is ingested).
    """
    counts = _counts(tag_codes)
    if not counts or not rules.lessons:
        return []
    engine = engine or get_engine()
    init_db(engine)
    by_pattern: dict[str, list] = {}
    for key, links in rules.lessons.items():
        by_pattern.setdefault(canonical(key), []).extend(links)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    with engine.connect() as conn:
        # Stable sort: highest count first, ties keep the rules-file order.
        order = {p: i for i, p in enumerate(by_pattern)}
        for pattern in sorted((p for p in counts if p in by_pattern),
                              key=lambda p: (-counts[p], order[p])):
            for link in by_pattern[pattern]:
                key = (pattern, link.lesson_id)
                if key in seen:
                    continue
                seen.add(key)
                pk = _resolve(conn, link.lesson_id)
                out.append({
                    "pattern": pattern, "lesson_id": link.lesson_id, "reason": link.reason,
                    "href": href_for(pk) if pk is not None else None,
                    "available": pk is not None,
                })
    return out


def check_lessons(rules: Rules, engine: Engine | None = None) -> list[str]:
    """Errors for mapped lesson ids missing from the local knowledge base.

    Empty when the corpus is not ingested yet (nothing to check against).
    """
    engine = engine or get_engine()
    if not rules.lessons or not corpus_ingested(engine):
        return []
    errors: list[str] = []
    with engine.connect() as conn:
        for pattern, links in rules.lessons.items():
            for link in links:
                if _resolve(conn, link.lesson_id) is None:
                    errors.append(f"lessons.{pattern}: unknown lesson id {link.lesson_id}")
    return errors
