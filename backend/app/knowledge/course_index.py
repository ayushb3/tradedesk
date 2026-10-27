"""Course-notes indexer: chunk private markdown notes into a self-contained cache.

The notes live OUTSIDE the repo (``TRADEDESK_COURSE_NOTES_DIR``) and are only read at
runtime. This module chunks them, embeds the chunks with the shared embedder and keeps
the result in its own SQLite cache file next to ``desk.db`` (``course_index.sqlite``), so
``desk.db`` needs no schema change. A manifest of (path, mtime, size) per source file
drives automatic rebuilds. Local file reads only: no network.

Expected layout under the notes dir (anything else is ignored):

- ``topics/<slug>.md``           chunked at every ``##`` / ``###`` heading  (kind "topic")
- ``module-<m>/lesson-<id>.md``  chunked at every ``##`` heading            (kind "sheet")
- ``claims/*.jsonl``             one JSON object per line                   (kind "claim")
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .embedder import Embedder

INDEX_VERSION = 1
CACHE_NAME = "course_index.sqlite"
SKIP_SECTIONS = {"related", "quiz"}
_MAX_EMBED_CHARS = 1500

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_VIDEO_RE = re.compile(r"^\*\*Video:\*\*\s*(\S+)", re.M)
_LESSON_FILE_RE = re.compile(r"^lesson-(.+)\.md$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


@dataclass
class Chunk:
    kind: str  # topic | sheet | claim
    slug: str | None  # topic slug (topic chunks; first tag for claims)
    lesson: str | None  # lesson id (sheets, claims)
    module: str | None
    section: str
    title: str
    video: str | None
    source: str  # path relative to the notes dir, forward slashes
    text: str
    extra: dict = field(default_factory=dict)

    def search_text(self) -> str:
        return f"{self.title}\n{self.section}\n{self.text}"


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def split_sections(body: str, levels: tuple[int, ...]) -> list[tuple[str, str]]:
    """Split markdown into (heading path, text) at the given heading levels.

    A ``###`` section is named ``"<parent ##> > <###>"``. Text before the first heading
    is returned under an empty heading.
    """
    out: list[tuple[str, str]] = []
    parent = ""
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            out.append((heading, text))

    for line in body.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*?)\s*$", line)
        if m and len(m.group(1)) in levels:
            flush()
            buf = []
            level, name = len(m.group(1)), m.group(2)
            if level == 2:
                parent, heading = name, name
            else:
                heading = f"{parent} > {name}" if parent else name
        else:
            buf.append(line)
    flush()
    return out


def _title_of(body: str, fallback: str) -> str:
    m = _H1_RE.search(body)
    return m.group(1).strip() if m else fallback


def chunk_topic(rel: str, raw: str) -> list[Chunk]:
    slug = Path(rel).stem
    body = strip_frontmatter(raw)
    title = _title_of(body, slug)
    related = related_topics(body, slug)
    chunks = []
    for heading, text in split_sections(body, (2, 3)):
        if not heading or heading.split(" > ")[0].strip().lower() in SKIP_SECTIONS:
            continue
        chunks.append(Chunk("topic", slug, None, None, heading, f"{title} ({slug})", None, rel, text,
                            {"related": related}))
    return chunks


def related_topics(body: str, slug: str) -> list[str]:
    """Topic slugs linked from the note's ``## Related`` section."""
    m = re.search(r"^##\s+Related\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    out: list[str] = []
    for w in _WIKILINK_RE.finditer(m.group(1) if m else ""):
        parts = w.group(1).split("/")
        if len(parts) >= 2 and parts[-2] == "topics" and parts[-1] != slug and parts[-1] not in out:
            out.append(parts[-1])
    return out


def lesson_id_from_path(rel: str) -> str | None:
    m = _LESSON_FILE_RE.match(Path(rel).name)
    return m.group(1) if m else None


def module_from_path(rel: str) -> str | None:
    parts = Path(rel).parts
    first = parts[0] if parts else ""
    return first[len("module-"):] if first.startswith("module-") else None


def chunk_sheet(rel: str, raw: str) -> list[Chunk]:
    lesson = lesson_id_from_path(rel)
    if lesson is None:
        return []
    body = strip_frontmatter(raw)
    title = _title_of(body, lesson)
    vm = _VIDEO_RE.search(body)
    video = vm.group(1) if vm else None
    module = module_from_path(rel)
    chunks = []
    for heading, text in split_sections(body, (2,)):
        if not heading or heading.strip().lower() in SKIP_SECTIONS:
            continue
        chunks.append(Chunk("sheet", None, lesson, module, heading, title, video, rel, text))
    return chunks


def chunk_claims(rel: str, raw: str) -> list[Chunk]:
    chunks = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not obj.get("claim"):
            continue
        tags = [str(t) for t in (obj.get("tags") or [])]
        lesson = str(obj["lesson"]) if obj.get("lesson") is not None else None
        text = str(obj["claim"])
        if obj.get("evidence"):
            text += f"\nEvidence: {obj['evidence']}"
        chunks.append(Chunk(
            "claim", tags[0] if tags else None, lesson, None, str(obj.get("kind") or "claim"),
            f"lesson {lesson}" if lesson else "claim", None, rel, text, {"tags": tags}))
    return chunks


def scan_sources(root: Path) -> dict[str, tuple[int, int]]:
    """The manifest: relative path -> (mtime_ns, size) for every indexable source file."""
    found: dict[str, tuple[int, int]] = {}
    for pat in ("topics/*.md", "module-*/lesson-*.md", "claims/*.jsonl"):
        for p in root.glob(pat):
            if p.is_file():
                st = p.stat()
                found[p.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return found


def collect_chunks(root: Path, manifest: dict[str, tuple[int, int]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for rel in sorted(manifest):
        raw = (root / rel).read_text(encoding="utf-8", errors="replace")
        if rel.startswith("topics/"):
            chunks += chunk_topic(rel, raw)
        elif rel.startswith("claims/"):
            chunks += chunk_claims(rel, raw)
        else:
            chunks += chunk_sheet(rel, raw)
    # Claims inherit module + video from their lesson's sheet.
    sheets = {c.lesson: c for c in chunks if c.kind == "sheet"}
    for c in chunks:
        if c.kind == "claim" and c.lesson in sheets:
            c.module = sheets[c.lesson].module
            c.video = sheets[c.lesson].video
    return chunks


# ------------------------------------------------------------------ cache file

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE manifest (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, kind TEXT, slug TEXT, lesson TEXT, module TEXT, section TEXT,
  title TEXT, video TEXT, source TEXT, text TEXT, extra TEXT, text_hash TEXT, vec BLOB
);
"""


def cache_path_for(db_path: str) -> Path:
    return Path(db_path).parent / CACHE_NAME


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))


def read_meta(cache: Path) -> tuple[dict, dict[str, tuple[int, int]]] | None:
    if not cache.exists():
        return None
    try:
        con = _connect(cache)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            man = {p: (m, s) for p, m, s in con.execute("SELECT path, mtime_ns, size FROM manifest")}
        finally:
            con.close()
        return meta, man
    except sqlite3.Error:
        return None


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _old_vectors(cache: Path, embedder_name: str) -> dict[str, bytes]:
    """text_hash -> vector blob from the previous build, when the embedder is unchanged."""
    got = read_meta(cache)
    if not got or got[0].get("embedder") != embedder_name or got[0].get("version") != str(INDEX_VERSION):
        return {}
    try:
        con = _connect(cache)
        try:
            return {h: v for h, v in con.execute("SELECT text_hash, vec FROM chunks") if h and v}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def is_fresh(cache: Path, manifest: dict[str, tuple[int, int]], embedder_name: str) -> bool:
    got = read_meta(cache)
    if not got:
        return False
    meta, old = got
    return (
        meta.get("version") == str(INDEX_VERSION)
        and meta.get("embedder") == embedder_name
        and old == manifest
    )


def build_index(root: Path, cache: Path, embedder: Embedder,
                manifest: dict[str, tuple[int, int]] | None = None) -> dict:
    """Rebuild the cache from scratch (reusing vectors for unchanged chunk text)."""
    manifest = manifest if manifest is not None else scan_sources(root)
    chunks = collect_chunks(root, manifest)
    reuse = _old_vectors(cache, embedder.name)
    hashes = [_hash(c.search_text()) for c in chunks]
    todo = [i for i, h in enumerate(hashes) if h not in reuse]
    vecs: dict[int, bytes] = {}
    batch = 64
    for s in range(0, len(todo), batch):
        idx = todo[s:s + batch]
        out = embedder.embed([chunks[i].search_text()[:_MAX_EMBED_CHARS] for i in idx])
        for i, v in zip(idx, out):
            vecs[i] = array("f", v).tobytes()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = _connect(tmp)
    try:
        con.executescript(_SCHEMA)
        built = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        con.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("version", str(INDEX_VERSION)), ("embedder", embedder.name),
            ("dim", str(embedder.dim)), ("built_at", built), ("notes_dir", str(root)),
        ])
        con.executemany("INSERT INTO manifest VALUES (?, ?, ?)",
                        [(p, m, s) for p, (m, s) in manifest.items()])
        con.executemany(
            "INSERT INTO chunks (kind, slug, lesson, module, section, title, video, source, text,"
            " extra, text_hash, vec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(c.kind, c.slug, c.lesson, c.module, c.section, c.title, c.video, c.source, c.text,
              json.dumps(c.extra), hashes[i], vecs[i] if i in vecs else reuse[hashes[i]])
             for i, c in enumerate(chunks)])
        con.commit()
    finally:
        con.close()
    tmp.replace(cache)
    return {"built": True, "chunks": len(chunks), "embedded": len(todo)}


def ensure_index(root: Path, cache: Path, embedder: Embedder) -> bool:
    """Rebuild when the file set / any mtime / size changed. Returns True if rebuilt."""
    manifest = scan_sources(root)
    if is_fresh(cache, manifest, embedder.name):
        return False
    build_index(root, cache, embedder, manifest)
    return True


@dataclass
class LoadedIndex:
    chunks: list[dict]
    vecs: list[array]
    meta: dict


def load_index(cache: Path) -> LoadedIndex:
    con = _connect(cache)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        chunks, vecs = [], []
        for row in con.execute(
            "SELECT id, kind, slug, lesson, module, section, title, video, source, text, extra, vec"
            " FROM chunks ORDER BY id"
        ):
            d = dict(zip(
                ("id", "kind", "slug", "lesson", "module", "section", "title", "video", "source",
                 "text", "extra"), row[:11]))
            d["extra"] = json.loads(d["extra"] or "{}")
            chunks.append(d)
            a = array("f")
            a.frombytes(row[11])
            vecs.append(a)
    finally:
        con.close()
    return LoadedIndex(chunks, vecs, meta)


def topic_lessons(raw: str) -> list[dict]:
    """Lessons cited by a topic note's wikilinks, in first-cited order, with cite counts."""
    seen: dict[str, dict] = {}
    for m in _WIKILINK_RE.finditer(raw):
        target = m.group(1)
        name = target.rsplit("/", 1)[-1]
        lm = re.match(r"^lesson-(.+)$", name)
        if not lm:
            continue
        lid = lm.group(1)
        mod = None
        for part in target.split("/"):
            if part.startswith("module-"):
                mod = part[len("module-"):]
        entry = seen.setdefault(lid, {"lesson": lid, "module": mod, "cited": 0})
        entry["cited"] += 1
    return list(seen.values())
