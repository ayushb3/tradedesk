"""Desk rules: typed model, loader, validator and round-tripping dump.

The personal file is ``data/rules.yaml`` (gitignored). On first load it is
copied from the committed ``backend/rules.example.yaml`` (placeholder numbers).
Override the path with ``TRADEDESK_RULES_PATH``. Times are stored as PT
wall-clock strings and converted with ``zoneinfo`` so DST is always right.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PATH = REPO_ROOT / "backend" / "rules.example.yaml"
DEFAULT_PATH = REPO_ROOT / "data" / "rules.yaml"
TZ_NAME = "America/Los_Angeles"

# Only these numeric fields are ever read from the broker export's account.json.
_ACCOUNT_NUMBER_FIELDS = ("equity", "cash", "buying_power", "portfolio_value")


class Window(BaseModel):
    start: str = "06:30"
    end: str = "08:30"


class LossLine(BaseModel):
    amount: float
    start_date: date


class AccountSize(BaseModel):
    source_path: str = "rh-export/out/account.json"
    fallback: float


class Rule(BaseModel):
    number: int
    text: str


class LessonLink(BaseModel):
    lesson_id: str
    reason: str = ""


class Rules(BaseModel):
    timezone: str = TZ_NAME
    window: Window = Field(default_factory=Window)
    daily_stop: float
    daily_target: float
    loss_line: LossLine
    default_risk_pct: float = 30
    account_size: AccountSize
    rules: list[Rule] = Field(default_factory=list)
    lessons: dict[str, list[LessonLink]] = Field(default_factory=dict)


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


def rules_path(path: str | os.PathLike | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("TRADEDESK_RULES_PATH")
    return Path(env) if env else DEFAULT_PATH


def validate(text: str) -> list[str]:
    """Return readable errors for a rules file; empty list means valid."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"not valid YAML: {exc}"]
    if not isinstance(raw, dict):
        return ["rules file must be a YAML mapping"]
    errors: list[str] = []
    if raw.get("daily_stop") is None:
        errors.append("missing daily_stop")
    if raw.get("daily_target") is None:
        errors.append("missing daily_target")
    try:
        model = Rules.model_validate(raw)
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            if loc in ("daily_stop", "daily_target") and e["type"] == "missing":
                continue
            errors.append(f"{loc}: {e['msg']}")
        return errors
    try:
        ZoneInfo(model.timezone)
    except Exception:
        errors.append(f"unknown timezone: {model.timezone}")
    try:
        start, end = _parse_hhmm(model.window.start), _parse_hhmm(model.window.end)
        if end <= start:
            errors.append(
                f"window end {model.window.end} is not after start {model.window.start}"
            )
    except ValueError:
        errors.append("window start/end must be HH:MM")
    if model.daily_stop >= 0:
        errors.append("daily_stop must be negative")
    if model.daily_target <= 0:
        errors.append("daily_target must be positive")
    if model.loss_line.amount <= 0:
        errors.append("loss_line.amount must be positive")
    elif model.daily_stop < 0 and -model.daily_stop > model.loss_line.amount:
        errors.append(
            f"daily_stop {model.daily_stop:g} is bigger than the whole loss line "
            f"{model.loss_line.amount:g}"
        )
    if not 0 < model.default_risk_pct <= 100:
        errors.append("default_risk_pct must be between 0 and 100")
    seen: set[int] = set()
    for r in model.rules:
        if r.number in seen:
            errors.append(f"duplicate rule number {r.number}")
        seen.add(r.number)
        if r.number < 1:
            errors.append(f"unknown rule number {r.number}")
    # Lesson patterns must be a known ledger tag code (or rule_<n>).
    from app.desk.lessons import is_known_pattern

    for pattern in model.lessons:
        if not is_known_pattern(pattern):
            errors.append(f"lessons: unknown pattern {pattern}")
        if pattern.startswith("rule_"):
            try:
                n = int(pattern.split("_", 1)[1])
            except ValueError:
                continue
            if n not in seen:
                errors.append(f"lessons cite unknown rule number {n}")
    return errors


def load_rules(path: str | os.PathLike | None = None) -> Rules:
    """Load rules; seeds the file from the example on first run."""
    p = rules_path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_PATH, p)
    text = p.read_text(encoding="utf-8")
    errors = validate(text)
    if errors:
        raise ValueError(f"invalid rules file {p.name}: " + "; ".join(errors))
    return Rules.model_validate(yaml.safe_load(text))


def dump_rules(rules: Rules) -> str:
    """Serialize to YAML that ``validate`` accepts and the loader reads back equal."""
    data = json.loads(rules.model_dump_json())
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def window_bounds(rules: Rules, day: date) -> tuple[datetime, datetime]:
    """Aware (start, end) of the trading window on ``day``; DST-correct."""
    tz = ZoneInfo(rules.timezone)
    return (
        datetime.combine(day, _parse_hhmm(rules.window.start), tzinfo=tz),
        datetime.combine(day, _parse_hhmm(rules.window.end), tzinfo=tz),
    )


def account_size(rules: Rules, base_dir: Path | None = None) -> float:
    """Account size from the export's account.json (numeric balance fields only).

    Never reads or returns any id/account-number field. Falls back to
    ``rules.account_size.fallback`` when the file or a usable number is absent.
    """
    src = Path(rules.account_size.source_path)
    if not src.is_absolute():
        src = (base_dir or REPO_ROOT) / src
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return float(rules.account_size.fallback)
    if isinstance(data, dict):
        for key in _ACCOUNT_NUMBER_FIELDS:
            v = data.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return float(v)
            if isinstance(v, str):
                try:
                    f = float(v.replace(",", ""))
                    if f > 0:
                        return f
                except ValueError:
                    pass
    return float(rules.account_size.fallback)
