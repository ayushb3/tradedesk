"""Desk MCP rules tools (issue #26): ``propose_rules`` and ``update_rules``.

``propose_rules`` validates a proposed rules YAML and returns the diff against the
current file; it writes nothing. ``update_rules`` writes only a file that validates,
only to the FIXED rules path (never an argument), after copying the old file to a
dated backup beside it. Both refuse during the trading window and when the loss line
is hit. Every call is audited with a sha256 of the YAML and a changed-keys summary,
not the full text. Nothing here touches the network.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.desk import days as desk_days
from app.desk.rules import EXAMPLE_PATH, load_rules, rules_path, validate, window_bounds

from . import mock
from .tools import Ctx, _meta
from .writes import PT, _audited

MOCK_RULES_NAME = "tradedesk-mock-rules.yaml"


# ----------------------------------------------------------------------- paths

def mock_rules_path() -> Path:
    return Path(tempfile.gettempdir()) / MOCK_RULES_NAME


def target_path(ctx: Ctx) -> Path:
    """The one file these tools may read/write. Mock mode never touches the real file;
    its temp copy is seeded from the committed example."""
    if ctx.mode == "mock":
        p = mock_rules_path()
        if not p.exists():
            shutil.copyfile(EXAMPLE_PATH, p)
        return p
    return rules_path()


def _current_text(ctx: Ctx) -> str:
    p = target_path(ctx)
    if not p.exists():
        return EXAMPLE_PATH.read_text(encoding="utf-8")
    return p.read_text(encoding="utf-8")


def _reload(ctx: Ctx, p: Path) -> None:
    new = load_rules(p)
    if ctx.mode == "mock":
        new.account_size.source_path = mock.mock_rules().account_size.source_path
    ctx.rules = new


# ---------------------------------------------------------------------- guards

def _guard(ctx: Ctx, now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pt = now.astimezone(PT)
    if pt.weekday() < 5:
        start, end = window_bounds(ctx.rules, pt.date())
        if start <= pt <= end:
            raise ValueError(
                f"rules are locked during the trading window ({ctx.rules.window.start}-"
                f"{ctx.rules.window.end} PT, weekdays); it is {pt:%H:%M} PT. Try after the window."
            )
    if desk_days.line_status(now=now, rules=ctx.rules, engine=ctx.engine).line_hit:
        raise ValueError("the loss line is hit; rules are locked. Stop and step away.")
    return now


# ------------------------------------------------------------------ summarising

def _scalars(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if k in ("rules", "lessons"):
            continue
        if isinstance(v, dict):
            out.update(_scalars(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _rule_map(d: dict) -> dict:
    return {r["number"]: r.get("text") for r in d.get("rules") or []
            if isinstance(r, dict) and "number" in r}


def summarize_changes(old_text: str, new_text: str) -> list[str]:
    """Readable changed keys, e.g. ``daily_stop -100 -> -300``, ``rule 4 edited``."""
    old = yaml.safe_load(old_text) or {}
    new = yaml.safe_load(new_text) or {}
    out: list[str] = []
    a, b = _scalars(old), _scalars(new)
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append(f"{k} {a.get(k, '(unset)')} -> {b.get(k, '(unset)')}")
    ra, rb = _rule_map(old), _rule_map(new)
    for n in sorted(set(rb) - set(ra)):
        out.append(f"rule {n} added")
    for n in sorted(set(ra) - set(rb)):
        out.append(f"rule {n} removed")
    for n in sorted(set(ra) & set(rb)):
        if ra[n] != rb[n]:
            out.append(f"rule {n} edited")
    la, lb = old.get("lessons") or {}, new.get("lessons") or {}
    for p in sorted(set(lb) - set(la)):
        out.append(f"lessons {p} added")
    for p in sorted(set(la) - set(lb)):
        out.append(f"lessons {p} removed")
    for p in sorted(set(la) & set(lb)):
        if la[p] != lb[p]:
            out.append(f"lessons {p} edited")
    return out


def _diff(old_text: str, new_text: str, now: datetime) -> str:
    stamp = now.astimezone(PT).strftime("%Y-%m-%d %H:%M PT")
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile="rules.yaml (current)", tofile=f"rules.yaml (proposed {stamp})", n=2,
    ))


def _digest(yaml_text: str) -> str:
    # 16 hex chars: long enough to identify a file, short enough that the audit
    # scrubber (which redacts 24+ hex runs as possible ids) leaves it alone.
    return hashlib.sha256(str(yaml_text).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------- tools

def propose_rules(ctx: Ctx, yaml_text: str, now: datetime | None = None) -> dict:
    """Validate ``yaml_text`` and return the diff against the current file. Writes nothing."""
    args: dict = {"yaml_sha256": _digest(yaml_text)}

    def run() -> dict:
        when = _guard(ctx, now)
        errors = validate(yaml_text)
        old = _current_text(ctx)
        changed = [] if errors else summarize_changes(old, yaml_text)
        args["changed"] = changed
        return {
            **_meta(ctx), "valid": not errors, "errors": errors,
            "diff": _diff(old, yaml_text, when), "summary": changed,
        }

    return _audited(ctx, "propose_rules", args, run)


def update_rules(
    ctx: Ctx, yaml_text: str, confirm: bool = False, now: datetime | None = None
) -> dict:
    """Write a validated rules file to the fixed path, keeping a dated backup first."""
    args: dict = {"yaml_sha256": _digest(yaml_text), "confirm": bool(confirm)}

    def run() -> dict:
        if confirm is not True:
            raise ValueError("update_rules needs confirm=true after the diff was approved")
        when = _guard(ctx, now)
        errors = validate(yaml_text)
        if errors:
            raise ValueError("rules file is invalid: " + "; ".join(errors))
        p = target_path(ctx)
        changed = summarize_changes(_current_text(ctx), yaml_text)
        args["changed"] = changed
        p.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if p.exists():
            backup = p.with_name(f"rules.backup-{when.astimezone(PT):%Y%m%d-%H%M%S}.yaml")
            shutil.copyfile(p, backup)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".rules-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(yaml_text)
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        _reload(ctx, p)
        return {
            **_meta(ctx), "written": True, "path": str(p),
            "backup": backup.name if backup else None, "summary": changed,
        }

    return _audited(ctx, "update_rules", args, run)
