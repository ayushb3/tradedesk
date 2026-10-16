"""Parse lesson transcript filenames and derive lesson -> module mapping.

Expected inputs (a plain-text corpus you supply yourself):

- ``<lessons dir>/NNN_Title.txt`` -- one transcript per lesson. The leading
  ``NNN`` is the global lesson number (our ``knowledge_lessons.id``); the rest is a
  sanitised title fragment (truncated, special chars replaced by ``_``).
- ``<modules dir>/Module_*.txt`` -- the same transcripts concatenated per
  module, each lesson introduced by a ``LESSON:``/``TITLE:``/``DATE:`` header
  block (any other header lines, e.g. ``URL:``, are ignored). These are the
  authoritative source for **module membership** (PRD section 5.2): the
  per-lesson files lose that grouping.

Mapping strategy:

1. **Content match (primary).** Split each module file into lesson blocks and
   match a transcript to the block whose body contains a distinctive snippet of
   the transcript text. Robust to mangled titles and unique in practice.
2. **Filename-number fallback.** Some transcripts embed a ``M.L...`` number in
   the filename (e.g. ``061_3.2.3.3_Title`` -> module ``3.2``); if the content
   match is ambiguous/empty, fall back to that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SEQ_RE = re.compile(r"^(\d+)_?(.*)$")
_NUMTOK_RE = re.compile(r"^(\d+(?:\.\d+)*)")
_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


@dataclass
class LessonBlock:
    """One lesson as parsed from a module file."""

    module: str          # module label: "0", "1", "3.2", "12.1", …
    seq: int             # order within the module (1-based)
    title: str           # cleaned title
    body_norm: str       # normalised transcript body (for content matching)


@dataclass
class TranscriptFile:
    lesson_id: int       # global number from filename (e.g. 001)
    raw_title: str       # sanitised title fragment from filename
    path: Path
    text: str


def module_label_from_filename(fname: str) -> str:
    """Map ``Module_7.txt`` → ``7``, ``Module_00_Extra.txt`` → ``0``,
    ``Module_12.1.txt`` → ``12.1``."""
    b = Path(fname).name
    m = re.match(r"Module_([0-9.]+)", b)
    if not m:
        return b.replace("Module_", "").replace(".txt", "")
    lab = m.group(1).rstrip(".")
    if re.fullmatch(r"0+", lab):
        return "0"
    return lab.lstrip("0") or lab  # "01" -> "1", keep "12.1"


def clean_title(raw: str) -> str:
    """Trim the trailing scraper junk (`` -   -   Video - …``) off a title."""
    # Cut at the first " - " that is followed mostly by dashes/underscores/space.
    cut = re.split(r"\s[-–]\s", raw)
    title = cut[0].strip()
    # collapse leftover underscores/dashes runs
    title = re.sub(r"[_\s]+", " ", title).strip(" -_")
    return title or raw.strip()


def parse_transcript_filename(path: Path) -> TranscriptFile:
    """Extract the global lesson number and a title fragment from the filename."""
    stem = path.stem
    m = _SEQ_RE.match(stem)
    if not m:
        raise ValueError(f"cannot parse lesson number from {path.name!r}")
    lesson_id = int(m.group(1))
    raw_title = m.group(2).replace("_", " ").strip()
    text = path.read_text(encoding="utf-8", errors="replace")
    return TranscriptFile(lesson_id=lesson_id, raw_title=raw_title, path=path, text=text)


def parse_module_file(path: Path) -> list[LessonBlock]:
    """Split one module file into ordered LessonBlocks."""
    txt = path.read_text(encoding="utf-8", errors="replace")
    module = module_label_from_filename(path.name)
    blocks: list[LessonBlock] = []
    # Everything before the first LESSON: is the file header — drop it.
    parts = re.split(r"(?m)^LESSON:\s*", txt)[1:]
    for i, part in enumerate(parts, start=1):
        first_line = part.splitlines()[0].strip() if part else ""
        # body follows the dashed separator after the DATE line
        seg = re.split(r"(?m)^-{10,}\s*$", part)
        body = seg[-1] if len(seg) > 1 else part
        blocks.append(
            LessonBlock(
                module=module,
                seq=i,
                title=clean_title(first_line),
                body_norm=_norm(body),
            )
        )
    return blocks


@dataclass
class ResolvedLesson:
    lesson_id: int
    module: str
    module_seq: int
    title: str
    source_path: Path
    raw_text: str


@dataclass
class MappingResult:
    lessons: list[ResolvedLesson] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)


def _module_seq_within(module: str, blocks: list[LessonBlock], block: LessonBlock) -> int:
    return block.seq


def build_mapping(lessons_dir: Path, modules_dir: Path) -> MappingResult:
    """Resolve every transcript to (module, seq, title).

    Returns lessons sorted by global lesson_id, plus any ids that could not be
    resolved (normally empty).
    """
    blocks: list[LessonBlock] = []
    for mf in sorted(modules_dir.glob("Module_*.txt")):
        blocks.extend(parse_module_file(mf))

    tx_files = sorted(lessons_dir.glob("*.txt"))
    result = MappingResult()

    for path in tx_files:
        tf = parse_transcript_filename(path)
        body = _norm(tf.text)
        block = _match_block(body, tf, blocks)
        if block is None:
            result.unresolved.append(tf.lesson_id)
            continue
        result.lessons.append(
            ResolvedLesson(
                lesson_id=tf.lesson_id,
                module=block.module,
                module_seq=block.seq,
                title=block.title or clean_title(tf.raw_title),
                source_path=path,
                raw_text=tf.text,
            )
        )

    result.lessons.sort(key=lambda l: l.lesson_id)
    return result


def _match_block(
    body_norm: str, tf: TranscriptFile, blocks: list[LessonBlock]
) -> LessonBlock | None:
    # 1) content snippet containment (unique in practice)
    if body_norm:
        for frac in (0.2, 0.4, 0.05):
            start = min(int(len(body_norm) * frac), max(0, len(body_norm) - 90))
            snip = body_norm[start : start + 80]
            if not snip:
                continue
            hits = [b for b in blocks if snip in b.body_norm]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                # ambiguous — prefer the longest common containment; fall through
                break

    # 2) filename module-number fallback (e.g. 061_3.2.3.3_… -> module "3.2")
    numtok = _NUMTOK_RE.match(tf.raw_title.replace(" ", ""))
    if numtok:
        parts = numtok.group(1).split(".")
        candidates = {parts[0], ".".join(parts[:2])} if len(parts) > 1 else {parts[0]}
        mod_blocks = [b for b in blocks if b.module in candidates]
        if mod_blocks:
            # pick the block whose title best matches the filename title
            want = _norm(clean_title(tf.raw_title))
            best = max(
                mod_blocks,
                key=lambda b: _title_overlap(want, _norm(b.title)),
            )
            return best
    return None


def _title_overlap(a: str, b: str) -> int:
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb)
