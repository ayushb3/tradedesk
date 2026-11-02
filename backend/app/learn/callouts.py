"""Drills from real calls the callout listener captured on the live trading call.

The listener (a separate local tool) writes ``callouts/callouts-YYYY-MM-DD.jsonl`` and, when
the owner hand-labels a day, ``labels/label-YYYY-MM-DD.csv`` under its local data dir. Both are
private: they are read here, turned into drill files in the private learn folder, and never
copied into the repo. Drill text never quotes the call.

An ``entry`` callout becomes a drill paused at the minute it was called: was there a trade
here, which way, where does the stop go, where is the target? The direction is the caller's.
Stop and target come from the course rule, not from the call (calls rarely say them): the stop
one cent past the last closed candle on the auto chart, the target the nearest level the chart
shows that pays at least MIN_RR. The level is the nearest of those levels on the stop side.

Which calls count: on a labelled day only rows marked real (``y``), with the labeller's ticker
correction; otherwise only confident calls with no listener flags.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from app.models.market import Candle

from . import ltp
from .detectors import Candidate

UTC = timezone.utc
MIN_CONFIDENCE = 0.8
MIN_RR = 1.5
MAX_RR = 6.0
MIN_RISK = 0.0004
# the chart shows an index call on its ETF (course: trade SPX off SPY charts and levels)
PROXY = {"SPX": "SPY", "SPXW": "SPY", "ES": "SPY", "NDX": "QQQ", "NQ": "QQQ"}


@dataclass
class Call:
    ticker: str          # chart ticker (after PROXY)
    called: str          # ticker as called
    side: str            # "long" | "short"
    at: datetime         # wall clock, UTC
    exits: list[datetime]   # later trim / exit calls on the same ticker group (UTC)


def default_dir() -> Path | None:
    """The listener's local data dir (``callout-listener where``), when it exists."""
    base = os.environ.get("LOCALAPPDATA")
    p = Path(base) / "callout-listener" if base else None
    return p if p and p.is_dir() else None


def _labels(root: Path, day: date) -> dict[tuple[str, str], dict] | None:
    f = root / "labels" / f"label-{day.isoformat()}.csv"
    if not f.is_file():
        return None
    out = {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            out[(r.get("time", ""), r.get("type", ""))] = r
    return out


def _wall(d: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(d["wall"]).astimezone(UTC)
    except (KeyError, TypeError, ValueError):
        return None


def load_calls(root: Path | None, day: date) -> list[Call]:
    if root is None:
        return []
    f = root / "callouts" / f"callouts-{day.isoformat()}.jsonl"
    if not f.is_file():
        return []
    labels = _labels(root, day)
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue

    def keep(d: dict) -> tuple[bool, str | None]:
        """(real?, corrected ticker)."""
        w = _wall(d)
        if w is None or "restated" in (d.get("flags") or []):
            return False, None
        if labels is not None:
            lab = labels.get((datetime.fromisoformat(d["wall"]).strftime("%H:%M:%S"), d["type"]))
            if not lab or lab.get("real?(y/n)", "").strip().lower() != "y":
                return False, None
            return True, (lab.get("correct_ticker") or "").strip().upper() or None
        return (float(d.get("confidence") or 0) >= MIN_CONFIDENCE and not d.get("flags")), None

    events = []
    for d in rows:
        ok, fix = keep(d)
        if ok:
            tk = fix or (d.get("ticker") or "").upper()
            if tk:
                events.append((d, tk, _wall(d)))
    calls = []
    for d, tk, w in events:
        if d["type"] != "entry" or d.get("direction") not in ("call", "put"):
            continue
        chart = PROXY.get(tk, tk)
        exits = [w2 for d2, tk2, w2 in events if d2["type"] in ("trim", "exit") and w2 > w
                 and PROXY.get(tk2, tk2) == chart]
        calls.append(Call(chart, tk, "long" if d["direction"] == "call" else "short", w,
                          sorted(exits)))
    return calls


def call_candidate(call: Call, bars: Sequence[Candle], day: str,
                   ctx: ltp.SessionContext | None = None) -> Candidate | None:
    """The drill setup for one call, or None when the chart cannot carry a rule-shaped plan."""
    pause = call.at.replace(second=0, microsecond=0)
    c = [b for b in bars if b.ts < pause]
    if len(c) < 20:
        return None
    open_utc = bars[0].ts.replace(second=0, microsecond=0)
    auto = ltp.auto_bars(c, open_utc)
    closed = [b for b in auto if b.end <= pause]
    if not closed:
        return None
    long_ = call.side == "long"
    px = c[-1].close
    # stop: past the last closed candle; widen to the last two when that is noise
    for k in (1, 2, 3):
        seg = closed[-k:]
        stop = round(min(b.low for b in seg) - 0.01, 2) if long_ else \
            round(max(b.high for b in seg) + 0.01, 2)
        if (px - stop if long_ else stop - px) / px >= MIN_RISK:
            break
    risk = px - stop if long_ else stop - px
    if risk <= 0 or risk / px < MIN_RISK:
        return None
    vw = ltp._vwaps(c, auto)[-1]
    orb = [b for b in c if b.ts < open_utc + timedelta(minutes=15)]
    levels = {**(ctx.levels if ctx else {}), "VWAP": vw,
              **ltp._hourly(c, open_utc, pause)}
    if orb and pause >= open_utc + timedelta(minutes=15):
        levels.update({"ORB high": max(b.high for b in orb), "ORB low": min(b.low for b in orb)})
    hod, lod = max(b.high for b in c), min(b.low for b in c)
    pays = {n: p for n, p in {**levels, "High of day": hod, "Low of day": lod}.items()
            if (p - px if long_ else px - p) >= MIN_RR * risk}
    if not pays:
        return None
    tname, target = (min(pays.items(), key=lambda x: x[1]) if long_
                     else max(pays.items(), key=lambda x: x[1]))
    if abs(target - px) > MAX_RR * risk:
        return None
    behind = {n: p for n, p in levels.items() if (p <= px if long_ else p >= px)}
    lname, level = (max(behind.items(), key=lambda x: x[1]) if long_
                    else min(behind.items(), key=lambda x: x[1])) if behind else ("Stop side", stop)
    facts = {"kind": "sup" if long_ else "res", "called": call.called,
             "called_at": call.at.isoformat(), "exits": [e.isoformat() for e in call.exits],
             "level_name": lname, "target_name": tname, "entry": round(px, 2),
             "rr": round(abs(target - px) / risk, 2), "bar_minutes": closed[-1].minutes,
             "vwap": round(vw, 2)}
    return Candidate(detector="somesh_call", ticker=call.ticker, date=day, pause_at=pause,
                     level=round(level, 2), side=call.side, strength=1.0, enter=True,
                     stop=stop, target=round(target, 2), facts=facts)
