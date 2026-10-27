"""Nightly drill builder:  python -m app.learn.build [--for-date D] [--days N] [--tickers A,B]
                                                        [--dry-run] [--candidates-only]

Reads cached 1m bars (and, when market-data keys are present, fetches missing sessions through
the existing read-only Alpaca market-data provider), runs the pure detectors, derives an answer
key mechanically from each setup, ties it to course notes through the course search, and writes
the best three drills to LEARN_DIR/drills/<for-date>/.

Hard rules: market data only; answer keys cite course notes only (a candidate with no
supporting course hit is dropped, never forced); fewer than three drills is fine.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from app.models.market import Candle

from . import detectors as det
from . import store

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

DEFAULT_TICKERS = ("SPY", "QQQ")
DEFAULT_DAYS = 10
PICK = 3
LOOKAHEAD_MIN = 90          # minutes of bars after the pause revealed with the result
MIN_SESSION_BARS = 250      # a regular session with fewer cached 1m bars is treated as missing
MIN_TOPIC_SCORE = 0.6       # top topic hit weaker than this -> no course support -> drop

# Generic trading phrases used to find the matching course material. "topic" picks the drill's
# topic + lessons; the others find the rule cited for each step.
RULE_QUERIES: dict[str, dict[str, str]] = {
    "level_touches": {
        "topic": "support and resistance level tested multiple times",
        "level": "price level touched several times support resistance",
        "enter": "wait for confirmation that the level holds before entering",
        "stop": "stop just past the candle at the level",
        "target": "target the next level",
    },
    "break_retest": {
        "topic": "break and retest of a level",
        "level": "level breaks then price comes back to retest it",
        "enter": "wait for the retest to hold before entering",
        "stop": "stop below the retest candle",
        "target": "target the next level or prior high",
    },
    "patience_candle_at_level": {
        "topic": "patience candle at a level",
        "level": "patience candle after a pullback to a level",
        "enter": "enter when the patience candle holds the level",
        "stop": "stop below the patience candle",
        "target": "target the next level",
    },
}
SHORT = {"level_touches": "levels", "break_retest": "retest", "patience_candle_at_level": "patience"}


# ------------------------------------------------------------------- rule lookup

@dataclass
class RuleInfo:
    topic: str
    topic_score: float
    lessons: list[str]
    refs: dict[str, str] = field(default_factory=dict)


def _hit_ref(res: dict) -> str | None:
    """'<lesson> § <section>' from the best sheet (or claim) hit, else '<topic> § <section>'."""
    for item in list(res.get("lessons", [])) + list(res.get("claims", [])):
        if item.get("lesson"):
            return f"{item['lesson']} § {item.get('section', '')}".strip(" §")
    for item in res.get("topics", []):
        if item.get("slug"):
            return f"topic {item['slug']} § {item.get('section', '')}".strip(" §")
    return None


class RuleBook:
    """Course-search lookups, cached per detector (queries do not depend on the candidate)."""

    def __init__(self, notes, min_score: float | None = None) -> None:
        self.notes = notes
        self.min_score = MIN_TOPIC_SCORE if min_score is None else min_score
        self._cache: dict[str, RuleInfo | None] = {}

    def lookup(self, detector: str) -> RuleInfo | None:
        if detector not in self._cache:
            try:
                self._cache[detector] = self._lookup(detector)
            except Exception as exc:  # a broken notes dir must never take the builder down
                print(f"course search failed for {detector}: {type(exc).__name__}", file=sys.stderr)
                self._cache[detector] = None
        return self._cache[detector]

    def _lookup(self, detector: str) -> RuleInfo | None:
        q = RULE_QUERIES.get(detector)
        if not q or self.notes is None:
            return None
        res = self.notes.search(q["topic"], k=10)
        topics = res.get("topics") or []
        if not topics:
            return None
        order = res.get("topic_order") or []
        slug = order[0] if order else topics[0]["slug"]
        score = max((t.get("score", 0.0) for t in topics if t.get("slug") == slug), default=0.0)
        if score < self.min_score:
            return None
        lessons: list[str] = []
        refs: dict[str, str] = {}
        topic_ref = _hit_ref(res)
        for step in ("level", "enter", "stop", "target"):
            r = self.notes.search(q[step], k=8)
            refs[step] = _hit_ref(r) or topic_ref or f"topic {slug}"
            for item in list(r.get("lessons", [])) + list(r.get("claims", [])):
                lid = item.get("lesson")
                if lid and lid not in lessons:
                    lessons.append(lid)
        for item in list(res.get("lessons", [])) + list(res.get("claims", [])):
            lid = item.get("lesson")
            if lid and lid not in lessons:
                lessons.insert(0, lid)
        if len(lessons) < 2:
            try:
                for e in sorted(self.notes.lessons_for(slug).get("lessons", []),
                                key=lambda x: -x.get("cited", 0)):
                    if e["lesson"] not in lessons:
                        lessons.append(e["lesson"])
            except Exception:
                pass
        return RuleInfo(topic=slug, topic_score=round(score, 3), lessons=lessons[:3], refs=refs)


# ------------------------------------------------------------------- explanation

def _pt_label(ts: datetime) -> str:
    t = ts.astimezone(PT)
    return t.strftime("%I:%M %p PT").lstrip("0")


def _cite(refs: dict[str, str], step: str) -> str:
    return f" (rule: {refs[step]})" if refs.get(step) else ""


def explain(c: det.Candidate, key: store.AnswerKey, refs: dict[str, str]) -> str:
    f = c.facts
    kind = "resistance" if f.get("kind") == "res" else "support"
    where = f"${key.level:.2f}"
    head = (f"**{c.ticker} on {c.date}, paused at {_pt_label(c.pause_at)}.** "
            f"The level in play is {where} ({kind}), touched {f.get('touches', 1)} time(s) so far.")
    if c.detector == "level_touches":
        lvl = (f"Price tested {where} {f.get('touches', 2)} times earlier, moved away, "
               f"and is now back at it for the first time since.")
        ent_yes = "The setup bar was rejected at the level (it closed back away from it), so the entry condition holds."
        ent_no = "The setup bar is at the level but has not been rejected yet, so the entry condition does not hold. Wait."
    elif c.detector == "break_retest":
        lvl = (f"{where} broke {f.get('bars_since_break', '?')} bars earlier and price is "
               f"now back to retest it without losing it.")
        ent_yes = "The retest bar held the level and closed in the break direction, so the entry condition holds."
        ent_no = "The retest touched the level but has not confirmed (no close in the break direction), so wait."
    else:
        lvl = (f"After a pullback into {where}, a small candle ({f.get('range_vs_avg', '?')}x the "
               f"recent average range) is sitting on the level.")
        ent_yes = "The patience candle is an inside bar that holds the level, so the entry condition holds."
        ent_no = "The candle is small but is either not an inside bar or does not hold the level, so wait."
    lines = [head, "", f"- **Level:** {lvl}{_cite(refs, 'level')}"]
    if key.enter == "enter":
        ext = c.facts.get("setup_low") if key.side == "long" else c.facts.get("setup_high")
        lines += [
            f"- **Enter or wait:** {ent_yes} Direction: {key.side}.{_cite(refs, 'enter')}",
            f"- **Stop ${key.stop:.2f}:** just past the setup candle's "
            f"{'low' if key.side == 'long' else 'high'} (${ext:.2f}) and the level, "
            f"whichever is further.{_cite(refs, 'stop')}",
            f"- **Target ${key.target:.2f}:** the next level in the trade direction, at least "
            f"{det.MIN_RR:g}x the risk away.{_cite(refs, 'target')}",
        ]
    else:
        lines.append(f"- **Enter or wait:** {ent_no}{_cite(refs, 'enter')}")
    return "\n".join(lines)


# ----------------------------------------------------------------------- drafting

def draft_drill(c: det.Candidate, info: RuleInfo, for_date: str, review: bool,
                drill_id: str = "") -> store.Drill:
    enter = c.enter
    key = store.AnswerKey(
        level=c.level, enter="enter" if enter else "wait",
        side=c.side if enter else None,
        stop=c.stop if enter else None,
        target=c.target if enter else None,
    )
    day = date.fromisoformat(c.date)
    start = datetime.combine(day, time(9, 30), ET).astimezone(UTC)
    close = datetime.combine(day, time(16, 0), ET).astimezone(UTC)
    until = min(c.pause_at + timedelta(minutes=LOOKAHEAD_MIN), close)
    rub = store.Rubric(rule_refs=dict(info.refs))
    return store.Drill(
        id=drill_id, set_date=for_date, topic=info.topic, lessons=list(info.lessons),
        ticker=c.ticker, date=c.date, timeframe="1m", bars_from=start, pause_at=c.pause_at,
        bars_until=until, question_steps=list(store.STEPS), answer_key=key,
        grading_rubric=rub, explanation=explain(c, key, info.refs),
        detector=c.detector, review=review,
    )


def select(drafts: list[tuple[det.Candidate, RuleInfo]], n: int = PICK
           ) -> list[tuple[det.Candidate, RuleInfo]]:
    """Best n by strength, spread across detector / ticker / date."""
    pool = sorted(drafts, key=lambda x: -x[0].strength)
    chosen: list[tuple[det.Candidate, RuleInfo]] = []
    while pool and len(chosen) < n:
        def adj(x):
            c = x[0]
            pen = sum(0.25 * (o.detector == c.detector) + 0.15 * (o.ticker == c.ticker)
                      + 0.15 * (o.date == c.date) for o, _ in chosen)
            return c.strength - pen
        best = max(pool, key=adj)
        pool.remove(best)
        chosen.append(best)
    return chosen


# ------------------------------------------------------------------------ bars

def rth_window(day: date) -> tuple[datetime, datetime]:
    return (datetime.combine(day, time(9, 30), ET).astimezone(UTC),
            datetime.combine(day, time(16, 0), ET).astimezone(UTC))


def read_cached(db_path: str, ticker: str, day: date) -> list[Candle]:
    """Regular-session 1m bars straight from candles_cache (read-only; never creates a DB)."""
    if not db_path or not Path(db_path).is_file():
        return []
    start, end = rth_window(day)
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT ts_utc, open, high, low, close, volume FROM candles_cache "
            "WHERE symbol=? AND timeframe='1m' AND ts_utc>=? AND ts_utc<? ORDER BY ts_utc",
            (ticker, start.replace(tzinfo=None).isoformat(sep=" "),
             end.replace(tzinfo=None).isoformat(sep=" ")),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for ts, o, h, l, c, v in rows:
        t = datetime.fromisoformat(str(ts))
        out.append(Candle(symbol=ticker, ts=t.replace(tzinfo=UTC) if t.tzinfo is None else t,
                          open=o, high=h, low=l, close=c, volume=v))
    return out


def have_alpaca_keys() -> bool:
    return bool(os.environ.get("APCA_API_KEY_ID", "").strip()
                and os.environ.get("APCA_API_SECRET_KEY", "").strip())


def make_loader(settings, allow_fetch: bool = True) -> Callable[[str, date], list[Candle]]:
    """cache first; a missing session is fetched via the existing Alpaca market-data provider
    (data.alpaca.markets, read-only, caches itself) when keys exist, else skipped."""
    state: dict = {}

    def load(ticker: str, day: date) -> list[Candle]:
        bars = read_cached(settings.db_path, ticker, day)
        if len(bars) >= MIN_SESSION_BARS or not allow_fetch or not have_alpaca_keys():
            return bars
        try:
            if "p" not in state:
                from app.providers.alpaca import AlpacaProvider

                state["p"] = AlpacaProvider(settings)
            got = state["p"].get_candles(ticker, day, "1m")
        except Exception as exc:
            print(f"fetch {ticker} {day} failed: {type(exc).__name__}", file=sys.stderr)
            return bars
        start, end = rth_window(day)
        return [b for b in got if start <= b.ts.astimezone(UTC) < end] or bars

    return load


# ------------------------------------------------------------------------ builder

def next_weekday_pt(now: datetime | None = None) -> date:
    d = store.today_pt(now) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def recent_weekdays(for_date: date, n: int) -> list[date]:
    out, d = [], for_date
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def build(for_date: date, days: int, tickers: list[str], *, loader: Callable[[str, date], list[Candle]],
          rules: RuleBook, root: Path | None, dry_run: bool = False,
          candidates_only: bool = False) -> dict:
    """Run the pipeline. Writes only when ``root`` is set and neither dry_run nor
    candidates_only. Returns a summary dict (drills are Drill models)."""
    fd = for_date.isoformat()
    state = store.read_state(root) if root else {"nights_built": 0, "last_built_for": None,
                                                 "built_dates": []}
    night = store.night_number(state, fd)
    review = store.review_for_night(night)

    counts = {k: 0 for k in det.DETECTORS}
    sessions = skipped = dropped = 0
    drafts: list[tuple[det.Candidate, RuleInfo]] = []
    for tk in tickers:
        for day in recent_weekdays(for_date, days):
            bars = loader(tk, day)
            if len(bars) < MIN_SESSION_BARS:
                skipped += 1
                continue
            sessions += 1
            for name, fn in det.DETECTORS.items():
                for cand in fn(bars, tk, day.isoformat()):
                    counts[name] += 1
                    info = rules.lookup(cand.detector)
                    if info is None:
                        dropped += 1
                        continue
                    drafts.append((cand, info))

    chosen = select(drafts, 8 if candidates_only else PICK)
    drills = [draft_drill(c, info, fd, review, f"{fd}-{c.ticker.lower()}-{SHORT[c.detector]}-{i}")
              for i, (c, info) in enumerate(chosen, 1)]
    out = {"for_date": fd, "night": night, "review": review, "sessions": sessions,
           "sessions_skipped": skipped, "candidates": counts, "dropped_no_course_support": dropped,
           "drills": drills, "written": []}
    if candidates_only:
        out["candidates_detail"] = [c.to_dict() for c, _ in chosen]
    if root and drills and not dry_run and not candidates_only:
        out["written"] = [str(p) for p in store.replace_set(root, fd, drills)]
        store.write_state(root, store.record_build(state, fd))
    return out


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m app.learn.build", description=__doc__.split("\n\n")[0])
    ap.add_argument("--for-date", help="PT date the set is for (default: next weekday in PT)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="recent weekdays to scan")
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    ap.add_argument("--dry-run", action="store_true", help="print the drills, write nothing")
    ap.add_argument("--candidates-only", action="store_true",
                    help="print candidate drafts as JSON for review, write nothing")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    from app.config import get_settings
    from .lessons import open_course_notes

    settings = get_settings()  # also loads .env so market-data keys are visible
    for_date = date.fromisoformat(a.for_date) if a.for_date else next_weekday_pt()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    root = store.learn_root(settings.learn_dir)
    read_only = a.dry_run or a.candidates_only
    if root is None and not read_only:
        print("learn folder not configured: set TRADEDESK_LEARN_DIR", file=sys.stderr)
        return 2
    notes = open_course_notes(settings.db_path)
    if notes is None:
        print("course notes not configured: set TRADEDESK_COURSE_NOTES_DIR; "
              "answer keys must cite course notes, so nothing was built", file=sys.stderr)
        return 2
    res = build(for_date, a.days, tickers, loader=make_loader(settings),
                rules=RuleBook(notes), root=root, dry_run=a.dry_run,
                candidates_only=a.candidates_only)
    drills = [d.model_dump(mode="json") for d in res["drills"]]
    summary = {k: v for k, v in res.items() if k != "drills"}  # incl. candidates_detail
    if read_only:
        print(json.dumps({**summary, "drills": drills}, indent=2))
    else:
        print(json.dumps(summary, indent=2))
        if not drills:
            print("no drills met the bar tonight (nothing written)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
