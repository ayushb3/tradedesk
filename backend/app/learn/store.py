"""Learn storage: drill models + the LEARN_DIR layout.

    LEARN_DIR/drills/YYYY-MM-DD/<drill_id>.json   one drill per file (the set for that PT date)
    LEARN_DIR/results.jsonl                       one line per answer; latest line per drill wins
    LEARN_DIR/builder-state.json                  {nights_built, last_built_for, built_dates}

Everything here is plain file IO. Nothing touches desk.db.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.models.utc import UtcDatetime

NOT_CONFIGURED = {"configured": False, "message": "learn folder not configured"}
STEPS = ("level", "enter", "stop", "target")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnswerKey(BaseModel):
    level: float
    enter: Literal["enter", "wait"]
    side: Literal["long", "short"] | None = None
    stop: float | None = None
    target: float | None = None


class Rubric(BaseModel):
    level_tol_pct: float = 0.05
    stop_tol_r: float = 0.35      # stop: full within this many R of the key stop (R = |level - key stop|)
    target_tol_r: float = 0.5     # target: full within this many R of the key target
    # legacy, kept so old drill files still load; grading no longer reads them
    stop_tol_pct: float = 0.1
    target_tol_pct: float = 0.15
    rule_refs: dict[str, str] = Field(default_factory=dict)


class Drill(BaseModel):
    """The full drill file (private: carries the answer key)."""

    id: str
    set_date: str
    topic: str
    lessons: list[str] = Field(default_factory=list)
    ticker: str
    date: str
    timeframe: str = "1m"
    bars_from: UtcDatetime
    pause_at: UtcDatetime
    bars_until: UtcDatetime
    question_steps: list[str] = Field(default_factory=lambda: list(STEPS))
    answer_key: AnswerKey
    grading_rubric: Rubric = Field(default_factory=Rubric)
    explanation: str = ""
    detector: str = ""
    review: bool = False


class LessonRef(BaseModel):
    id: str
    title: str | None = None
    video_url: str | None = None


class DrillPublic(BaseModel):
    """What the browser may see before answering: never the key, rubric or explanation."""

    id: str
    set_date: str
    topic: str
    lessons: list[LessonRef]
    ticker: str
    date: str
    timeframe: str
    bars_from: UtcDatetime
    pause_at: UtcDatetime
    question_steps: list[str]
    review: bool
    status: Literal["new", "done"] = "new"
    score: int | None = None


# ---------------------------------------------------------------------- layout

def learn_root(raw: str | None = None) -> Path | None:
    """The configured learn folder, or None (unset). The folder is created on first write."""
    if raw is None:
        from app.config import get_settings

        raw = get_settings().learn_dir
    raw = (raw or "").strip()
    return Path(raw).expanduser() if raw else None


def drills_dir(root: Path) -> Path:
    return root / "drills"


def set_dir(root: Path, set_date: str) -> Path:
    return drills_dir(root) / set_date


def list_set_dates(root: Path) -> list[str]:
    d = drills_dir(root)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and _DATE_RE.match(p.name))


def load_drill_file(path: Path) -> Drill | None:
    try:
        return Drill.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def load_set(root: Path, set_date: str) -> list[Drill]:
    d = set_dir(root, set_date)
    if not d.is_dir():
        return []
    out = [x for x in (load_drill_file(p) for p in sorted(d.glob("*.json"))) if x]
    return sorted(out, key=lambda x: x.id)


def find_drill(root: Path, drill_id: str) -> Drill | None:
    if not _ID_RE.match(drill_id or ""):  # also blocks path traversal
        return None
    for set_date in reversed(list_set_dates(root)):
        p = set_dir(root, set_date) / f"{drill_id}.json"
        if p.is_file():
            return load_drill_file(p)
    return None


def all_drills(root: Path) -> list[Drill]:
    out: list[Drill] = []
    for d in list_set_dates(root):
        out.extend(load_set(root, d))
    return out


def write_drill(root: Path, drill: Drill) -> Path:
    d = set_dir(root, drill.set_date)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{drill.id}.json"
    p.write_text(json.dumps(drill.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return p


def replace_set(root: Path, set_date: str, drills: list[Drill]) -> list[Path]:
    """Overwrite the whole set for a date (a re-run replaces, never accumulates)."""
    d = set_dir(root, set_date)
    if d.is_dir():
        for old in d.glob("*.json"):
            old.unlink()
    return [write_drill(root, x) for x in drills]


# --------------------------------------------------------------------- results

def results_path(root: Path) -> Path:
    return root / "results.jsonl"


def append_result(root: Path, row: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with results_path(root).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def latest_results(root: Path) -> dict[str, dict]:
    """drill_id -> its last result line (later lines win). Bad lines are skipped."""
    p = results_path(root)
    out: dict[str, dict] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("drill_id"):
            out[row["drill_id"]] = row
    return out


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------- builder state

def state_path(root: Path) -> Path:
    return root / "builder-state.json"


def read_state(root: Path) -> dict:
    try:
        raw = json.loads(state_path(root).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            dates = [d for d in raw.get("built_dates", []) if isinstance(d, str)]
            n = int(raw.get("nights_built", len(dates)))
            return {"nights_built": n, "last_built_for": raw.get("last_built_for"),
                    "built_dates": dates}
    except (OSError, ValueError, TypeError):
        pass
    return {"nights_built": 0, "last_built_for": None, "built_dates": []}


def write_state(root: Path, state: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    state_path(root).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def night_number(state: dict, for_date: str) -> int:
    """1-based night this date is: its place if already built, else the next one."""
    dates = state.get("built_dates") or []
    if for_date in dates:
        return dates.index(for_date) + 1
    return max(int(state.get("nights_built", 0)), len(dates)) + 1


def record_build(state: dict, for_date: str) -> dict:
    """State after building ``for_date``; rebuilding the same date never double-counts."""
    dates = list(state.get("built_dates") or [])
    if for_date in dates:
        nights = max(int(state.get("nights_built", 0)), len(dates))
    else:
        dates.append(for_date)
        nights = max(int(state.get("nights_built", 0)), len(dates) - 1) + 1
    return {"nights_built": nights, "last_built_for": for_date, "built_dates": dates}


def review_for_night(night: int) -> bool:
    """The first two nights of drills are flagged for a human look."""
    return night <= 2


def today_pt(now: datetime | None = None) -> date:
    from zoneinfo import ZoneInfo

    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Los_Angeles")).date()
