"""Hybrid (BM25 + embedding cosine) search over the private course-notes index.

Everything here is a local file read. Every public function returns a JSON-able dict and
never raises: an unset or missing notes dir yields a clear "not configured" payload.
"""
from __future__ import annotations

import math
import os
import re
from array import array
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import course_index as ci
from .embedder import Embedder, get_embedder

ENV_VAR = "TRADEDESK_COURSE_NOTES_DIR"
NOT_CONFIGURED = (
    "course notes not configured: set TRADEDESK_COURSE_NOTES_DIR to the notes folder "
    "(it must exist and contain topics/, module-*/ and claims/)."
)

# Scoring knobs (tuned on synthetic fixtures + a local private set).
BM25_K1 = 1.2
BM25_B = 0.75
W_BM25 = 0.65
W_COSINE = 0.35
TOPIC_BOOST = 1.6
ANSWER_SECTION_BOOST = 1.15
# Every topic note carries the same standard headings; their words say nothing about the
# subject, so they are not indexed as heading terms and the weaker sections are damped.
BOILERPLATE_SECTIONS = {
    "the answer": ANSWER_SECTION_BOOST,
    "how it works": 1.0,
    "where lessons disagree": 0.8,
    "small-account and 0dte notes": 0.8,
    "what the course doesn't cover": 0.6,
}
# A topic's rank = best section + these weights * its next-best sections + a small bonus
# from the best claims tagged with that topic (so a topic that is relevant all over wins
# over one that merely mentions a query word in a single section).
SECTION_RANK_WEIGHTS = (1.0, 0.5, 0.25)
CLAIM_SUPPORT_WEIGHT = 0.15
# Topic ranking also uses a whole-note BM25 (all of a topic's sections as one document):
# a note that is about the query terms all the way through beats one that mentions them
# once. Weight of that signal relative to the section-level one (both max-normalised).
TOPIC_DOC_WEIGHT = 1.0
# Topics linked from the leading topic's "Related" section get this bonus (same units),
# so neighbours of the best answer surface even when the query never names them
# (capped just below the leader).
RELATED_BONUS = 0.5
TITLE_REPEAT = 3
SECTION_REPEAT = 2
SNIPPET_CHARS = 260

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_STOP = frozenset(
    "a an and are as at be but by can do does for from has have how i if in into is it its of on "
    "or so that the their there they this to was what when where which who why will with you your "
    "should would could about than then them these those been being not no my me we our us am up out more much many some any very just also".split()
)
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


def _stem(tok: str) -> str:
    tok = tok.replace("'", "")
    for suf in ("ing", "ers", "er", "ed", "es", "s", "ly"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            if suf == "s" and tok.endswith("ss"):
                continue
            return tok[: -len(suf)]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


def clean_links(text: str) -> str:
    """Wikilinks -> their label (the lesson id), or the target's last path part."""
    return _LINK_RE.sub(lambda m: m.group(2) or m.group(1).rsplit("/", 1)[-1], text)


def _boiler(c: dict) -> bool:
    return c["kind"] == "topic" and c["section"].lower() in BOILERPLATE_SECTIONS


def _snippet(text: str, q_tokens: set[str]) -> str:
    text = clean_links(text)
    parts = [p.strip(" -\t") for p in re.split(r"\n+", text) if p.strip()]
    if not parts:
        return ""
    best = max(parts, key=lambda p: (len(q_tokens & set(tokenize(p))), -len(p)))
    best = re.sub(r"\s+", " ", best)
    return best if len(best) <= SNIPPET_CHARS else best[: SNIPPET_CHARS - 1].rstrip() + "…"


@dataclass
class _Ready:
    index: ci.LoadedIndex
    bm25_tf: list[Counter]
    doc_len: list[int]
    avg_len: float
    idf: dict[str, float]
    topic_tf: dict[str, Counter]
    topic_len: dict[str, int]
    topic_idf: dict[str, float]


_cache: dict[tuple, _Ready] = {}


def _prepare(idx: ci.LoadedIndex, stamp: tuple) -> _Ready:
    if stamp in _cache:
        return _cache[stamp]
    tfs: list[Counter] = []
    lens: list[int] = []
    df: Counter = Counter()
    for c in idx.chunks:
        toks = (
            tokenize(c["title"] + " " + (c["slug"] or "").replace("-", " ")) * TITLE_REPEAT
            + ([] if _boiler(c) else tokenize(c["section"]) * SECTION_REPEAT)
            + tokenize(c["text"])
        )
        tf = Counter(toks)
        tfs.append(tf)
        lens.append(len(toks))
        df.update(tf.keys())
    n = max(len(idx.chunks), 1)
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    topic_tf: dict[str, Counter] = {}
    topic_len: dict[str, int] = {}
    for c, tf, ln in zip(idx.chunks, tfs, lens):
        if c["kind"] == "topic":
            topic_tf.setdefault(c["slug"], Counter()).update(tf)
            topic_len[c["slug"]] = topic_len.get(c["slug"], 0) + ln
    tdf: Counter = Counter()
    for tf in topic_tf.values():
        tdf.update(tf.keys())
    nt = max(len(topic_tf), 1)
    topic_idf = {t: math.log(1 + (nt - d + 0.5) / (d + 0.5)) for t, d in tdf.items()}
    ready = _Ready(idx, tfs, lens, (sum(lens) / n) or 1.0, idf, topic_tf, topic_len, topic_idf)
    _cache.clear()
    _cache[stamp] = ready
    return ready


# --------------------------------------------------------------------- the KB

class CourseNotes:
    def __init__(self, root: Path, cache: Path, embedder: Embedder | None = None) -> None:
        self.root = root
        self.cache = cache
        self.embedder = embedder or get_embedder()

    def ready(self) -> _Ready:
        ci.ensure_index(self.root, self.cache, self.embedder)
        meta, _ = ci.read_meta(self.cache) or ({}, {})
        stamp = (str(self.cache), meta.get("built_at"), meta.get("embedder"))
        if stamp in _cache:
            return _cache[stamp]
        return _prepare(ci.load_index(self.cache), stamp)

    # ---- search
    def search(self, query: str, k: int = 8) -> dict:
        k = max(1, min(int(k), 50))
        r = self.ready()
        q_tokens = tokenize(query)
        if not q_tokens:
            return {"configured": True, "query": query, "message": "empty query", "topics": [],
                    "lessons": [], "claims": []}
        qset = set(q_tokens)
        qtf = Counter(q_tokens)
        n = len(r.index.chunks)
        bm = [0.0] * n
        for i, tf in enumerate(r.bm25_tf):
            s = 0.0
            for t in qtf:
                f = tf.get(t)
                if f:
                    s += r.idf.get(t, 0.0) * f * (BM25_K1 + 1) / (
                        f + BM25_K1 * (1 - BM25_B + BM25_B * r.doc_len[i] / r.avg_len))
            bm[i] = s
        qv = self.embedder.embed([query])[0]
        cos = [max(0.0, sum(a * b for a, b in zip(qv, v))) if len(v) == len(qv) else 0.0
               for v in r.index.vecs]
        bm_max = max(bm) or 1.0
        cos_max = max(cos) or 1.0
        scores = []
        for i, c in enumerate(r.index.chunks):
            s = W_BM25 * bm[i] / bm_max + W_COSINE * cos[i] / cos_max
            if c["kind"] == "topic":
                s *= TOPIC_BOOST * BOILERPLATE_SECTIONS.get(c["section"].lower(), 1.0)
            scores.append(s)

        def item(i: int) -> dict:
            c = r.index.chunks[i]
            out = {
                "kind": c["kind"], "slug": c["slug"], "lesson": c["lesson"], "module": c["module"],
                "section": c["section"], "video": c["video"], "source": c["source"],
                "score": round(scores[i], 4), "snippet": _snippet(c["text"], qset),
            }
            if c["kind"] != "topic":
                out["title"] = c["title"]
            if c["kind"] == "claim":
                out["claim_kind"] = c["section"]
                out["tags"] = c["extra"].get("tags", [])
            return {k_: v for k_, v in out.items() if v not in (None, "")}

        ranked = sorted((i for i in range(n) if scores[i] > 0), key=lambda i: -scores[i])
        by_kind = {kd: [i for i in ranked if r.index.chunks[i]["kind"] == kd]
                   for kd in ("topic", "sheet", "claim")}

        # Topics: rank topics by best section (+ runner-up), then pick best sections
        # round-robin so several topics show up before a topic's second section.
        per_topic: dict[str, list[int]] = {}
        for i in by_kind["topic"]:
            per_topic.setdefault(r.index.chunks[i]["slug"], []).append(i)
        support: dict[str, list[float]] = {}
        for i in by_kind["claim"]:
            for tag in r.index.chunks[i]["extra"].get("tags", []):
                support.setdefault(tag, []).append(scores[i])

        def topic_score(slug: str) -> float:
            secs = [scores[i] for i in per_topic[slug]]
            base = sum(w * v for w, v in zip(SECTION_RANK_WEIGHTS, secs))
            return base + CLAIM_SUPPORT_WEIGHT * sum(support.get(slug, [])[:3])

        avg_t = (sum(r.topic_len.values()) / max(len(r.topic_len), 1)) or 1.0

        def topic_doc_bm25(slug: str) -> float:
            tf, tot = r.topic_tf[slug], 0.0
            for t in qtf:
                f = tf.get(t)
                if f:
                    tot += r.topic_idf.get(t, 0.0) * f * (BM25_K1 + 1) / (
                        f + BM25_K1 * (1 - BM25_B + BM25_B * r.topic_len[slug] / avg_t))
            return tot

        sec_scores = {sl: topic_score(sl) for sl in per_topic}
        doc_scores = {sl: topic_doc_bm25(sl) for sl in per_topic}
        sec_max = max(sec_scores.values(), default=0.0) or 1.0
        doc_max = max(doc_scores.values(), default=0.0) or 1.0
        combined = {sl: sec_scores[sl] / sec_max + TOPIC_DOC_WEIGHT * doc_scores[sl] / doc_max
                    for sl in per_topic}
        if combined:
            leader = max(combined, key=combined.get)
            lead_chunk = r.index.chunks[per_topic[leader][0]]
            for sl in lead_chunk["extra"].get("related", []):
                if sl in combined:  # a neighbour may catch up, never overtake, the leader
                    combined[sl] = min(combined[sl] + RELATED_BONUS, combined[leader] - 1e-6)
        topic_rank = sorted(per_topic, key=lambda sl: -combined[sl])
        topic_q = max(2, k // 2)
        topics: list[int] = []
        for depth in range(2):
            for s in topic_rank:
                if len(topics) < topic_q and len(per_topic[s]) > depth:
                    topics.append(per_topic[s][depth])
        # sheets: one passage per lesson; claims: best first
        sheets, seen = [], set()
        for i in by_kind["sheet"]:
            lid = r.index.chunks[i]["lesson"]
            if lid not in seen:
                seen.add(lid)
                sheets.append(i)
        claims = by_kind["claim"]
        rest = k - len(topics)
        sheet_q = min(len(sheets), max(1, rest // 2)) if rest > 0 else 0
        claim_q = min(len(claims), rest - sheet_q)
        sheet_q = min(len(sheets), rest - claim_q)  # give unused claim slots to sheets
        return {
            "configured": True, "query": query, "embedder": self.embedder.name,
            "topic_order": topic_rank[:5],
            "topics": [item(i) for i in topics],
            "lessons": [item(i) for i in sheets[:sheet_q]],
            "claims": [item(i) for i in claims[:claim_q]],
        }

    # ---- direct reads
    def _topic_path(self, slug: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", slug or "") or slug.startswith("."):
            return None
        p = self.root / "topics" / f"{slug}.md"
        return p if p.is_file() else None

    def topic_slugs(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "topics").glob("*.md"))

    def resolve_topic(self, name: str) -> str | None:
        if self._topic_path(name):
            return name
        norm = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
        if self._topic_path(norm):
            return norm
        res = self.search(name, k=4).get("topics") or []
        return res[0]["slug"] if res else None

    def get_topic(self, slug: str) -> dict:
        p = self._topic_path(slug)
        if not p:
            return {"configured": True, "found": False, "slug": slug,
                    "message": f"no topic {slug!r}", "available": self.topic_slugs()}
        raw = p.read_text(encoding="utf-8", errors="replace")
        body = ci.strip_frontmatter(raw)
        return {"configured": True, "found": True, "slug": slug,
                "title": ci._title_of(body, slug), "source": f"topics/{slug}.md",
                "note": clean_links(body).strip()}

    def get_sheet(self, lesson_id: str) -> dict:
        lid = (lesson_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.]+", lid) or lid.startswith("."):
            return {"configured": True, "found": False, "lesson": lesson_id,
                    "message": "lesson id looks like 11.12 or 12.2.4"}
        hits = sorted(self.root.glob(f"module-*/lesson-{lid}.md"))
        if not hits:
            return {"configured": True, "found": False, "lesson": lid,
                    "message": f"no sheet for lesson {lid}"}
        raw = hits[0].read_text(encoding="utf-8", errors="replace")
        body = ci.strip_frontmatter(raw)
        vm = ci._VIDEO_RE.search(body)
        return {"configured": True, "found": True, "lesson": lid,
                "module": ci.module_from_path(hits[0].relative_to(self.root).as_posix()),
                "title": ci._title_of(body, lid), "video": vm.group(1) if vm else None,
                "source": hits[0].relative_to(self.root).as_posix(), "sheet": body.strip()}

    def lessons_for(self, topic: str) -> dict:
        slug = self.resolve_topic(topic)
        if not slug:
            return {"configured": True, "found": False, "topic": topic,
                    "message": f"no topic matching {topic!r}", "available": self.topic_slugs()}
        raw = self._topic_path(slug).read_text(encoding="utf-8", errors="replace")
        sheets = {c["lesson"]: c for c in self.ready().index.chunks if c["kind"] == "sheet"}
        lessons = []
        for e in ci.topic_lessons(raw):
            s = sheets.get(e["lesson"])
            lessons.append({**e, "title": s["title"] if s else None,
                            "video": s["video"] if s else None,
                            "has_sheet": s is not None})
        return {"configured": True, "found": True, "topic": slug, "count": len(lessons),
                "lessons": lessons}

    def status(self) -> dict:
        r = self.ready()
        counts = Counter(c["kind"] for c in r.index.chunks)
        return {
            "configured": True, "found": True, "notes_dir_set": True,
            "counts": {"topic": counts.get("topic", 0), "sheet": counts.get("sheet", 0),
                       "claim": counts.get("claim", 0), "total": len(r.index.chunks)},
            "topics": self.topic_slugs(),
            "files_indexed": len((ci.read_meta(self.cache) or ({}, {}))[1]),
            "last_build": r.index.meta.get("built_at"), "embedder": r.index.meta.get("embedder"),
            "cache_file": str(self.cache),
        }


# ------------------------------------------------------------ safe entry points

def notes_dir_setting() -> str:
    try:
        from app.config import get_settings

        v = get_settings().course_notes_dir
    except Exception:
        v = ""
    return (v or os.environ.get(ENV_VAR, "")).strip()


def open_notes(db_path: str) -> tuple[CourseNotes | None, dict | None]:
    """(notes, None) when usable, else (None, the "not configured" payload)."""
    raw = notes_dir_setting()
    if not raw:
        return None, {"configured": False, "notes_dir_set": False, "message": NOT_CONFIGURED}
    root = Path(raw).expanduser()
    if not root.is_dir():
        return None, {"configured": False, "notes_dir_set": True, "found": False,
                      "message": NOT_CONFIGURED + " (the configured folder was not found)"}
    return CourseNotes(root, ci.cache_path_for(db_path)), None


def run(db_path: str, fn: str, *args) -> dict:
    """Call ``CourseNotes.<fn>(*args)``; never raises."""
    notes, err = open_notes(db_path)
    if err is not None:
        if fn == "status":
            err = {**err, "counts": {}, "embedder": None, "last_build": None}
        return err
    try:
        return getattr(notes, fn)(*args)
    except Exception as exc:  # a broken notes dir must not take the MCP server down
        return {"configured": True, "error": f"course notes unavailable: {type(exc).__name__}: {exc}"}
