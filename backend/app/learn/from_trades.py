"""Drills from your own rule breaks: the trade-driven source for the nightly builder.

The market source (build.py) finds setups in recent sessions. This one first reads your own
trades of the last WINDOW_DAYS (rule results, ledger tags, excursion codes, the same numbers the
day page shows), names what went wrong, and then picks market setups that drill exactly that:

    exited_early               -> ``first_target``: an LTP enter setup that reached its first
                                  target. The drill pauses AT the first target: hold a runner
                                  (stop at break-even, the next level as the target) or not.
    countertrend (derived)     -> ``countertrend``: a setup whose side fights the auto chart's
                                  trend at the pause (EMA8 vs EMA21 and VWAP). The key is wait.
    before_cutoff (rule broken)-> ``late``: a setup that shows up after the window cutoff
                                  (rules.window.end, 8:30 PT by default). The key is wait.

A trade is countertrend when, at its first fill, the underlying's auto-chart trend points the
other way (a call in a downtrend, a put in an uptrend). Nothing else here is a judgment call.

Each drill made this way carries ``because`` = (trade id, session date, rule) so the card can
say "Because of your trade on <date>". Pure functions except ``load_signals`` (DB read). No
clock, no randomness: the same trades and bars always pick the same drills.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from app.models.market import Candle

from . import ltp
from .detectors import Candidate
from .outcome import play_out
from .store import AnswerKey, Because

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

WINDOW_DAYS = 7               # calendar days of trades read before the set date
DEFAULT_CUTOFF = time(8, 30)  # PT; the builder passes rules.window.end
TRADE_PICK = 2                # at most this many trade-driven drills per night
RUNNER_MIN_R = 1.0            # the runner's level must sit at least this many R past target 1
MIN_TREND_BARS = 8            # auto-chart bars needed before a trend is read at all

KINDS = ("first_target", "countertrend", "late")
RULE_FOR_KIND = {"first_target": "exited_early", "countertrend": "countertrend",
                 "late": "before_cutoff"}
SHORT = {"first_target": "target", "countertrend": "counter", "late": "late"}
# Chart symbol for an index option's underlying (bars are cached for the ETF).
PROXY = {"SPX": "SPY", "SPXW": "SPY", "XSP": "SPY", "NDX": "QQQ", "NDXP": "QQQ"}


@dataclass(frozen=True)
class TradeSignal:
    """One trade, reduced to what the drill picker needs.

    ``codes`` holds the codes of BROKEN rules (``before_cutoff``, ``daily_stop`` ...), the
    ledger tag codes (``added_lower``, ``held_loser``, ``gave_back`` ...), the excursion codes
    (``exited_early``, ``held_past_stop``) and, once marked, ``countertrend``."""

    trade_id: int
    date: str                      # session date YYYY-MM-DD
    ticker: str                    # traded underlying symbol
    side: str | None               # "long" (calls) | "short" (puts) | None (unknown)
    entry_ts: datetime | None      # first fill (UTC)
    codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Want:
    """A drill kind your trades asked for, and the trade it is because of."""

    kind: str
    because: Because
    count: int                     # trades in the window that asked for it
    ticker: str                    # chart ticker of the trade it is because of


def chart_ticker(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    return PROXY.get(s, s)


# ------------------------------------------------------------------- trend

def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def trend_at(bars: Sequence[Candle], ts: datetime) -> str | None:
    """'up' | 'down' | None: the auto chart's trend from the bars closed by ``ts``.

    Up = EMA8 above EMA21 and the last close above VWAP; down = the mirror; anything mixed,
    or too few bars to read, is None (never a guess)."""
    if not bars:
        return None
    t = _utc(ts)
    seen = [b for b in bars if _utc(b.ts) + timedelta(minutes=1) <= t]
    if not seen:
        return None
    open_utc = _utc(bars[0].ts).replace(second=0, microsecond=0)
    auto = ltp.auto_bars(seen, open_utc)
    if len(auto) < MIN_TREND_BARS:
        return None
    closes = [b.close for b in auto]
    e8, e21, vw = ltp._ema(closes, 8)[-1], ltp._ema(closes, 21)[-1], ltp._vwaps(seen, auto)[-1]
    if e8 > e21 and closes[-1] > vw:
        return "up"
    if e8 < e21 and closes[-1] < vw:
        return "down"
    return None


def against(side: str | None, trend: str | None) -> bool:
    return (side == "long" and trend == "down") or (side == "short" and trend == "up")


def mark_countertrend(sig: TradeSignal, bars: Sequence[Candle]) -> TradeSignal:
    """The signal with ``countertrend`` added when its entry fought the underlying's trend."""
    if sig.side is None or sig.entry_ts is None or "countertrend" in sig.codes:
        return sig
    if against(sig.side, trend_at(bars, sig.entry_ts)):
        return replace(sig, codes=sig.codes | {"countertrend"})
    return sig


# ------------------------------------------------------------------- wants

def kinds_for(sig: TradeSignal) -> list[str]:
    return [k for k in KINDS if RULE_FOR_KIND[k] in sig.codes]


def _latest_key(s: TradeSignal):
    return (s.date, _utc(s.entry_ts) if s.entry_ts else datetime.min.replace(tzinfo=UTC),
            s.trade_id)


def pick_wants(signals: Sequence[TradeSignal]) -> list[Want]:
    """One Want per kind your trades asked for: most trades first, then the most recent, then
    the KINDS order. Each is because of the latest trade that asked for it."""
    out: list[Want] = []
    for kind in KINDS:
        hits = [s for s in signals if kind in kinds_for(s)]
        if not hits:
            continue
        last = max(hits, key=_latest_key)
        out.append(Want(kind, Because(trade_id=last.trade_id, date=last.date,
                                      rule=RULE_FOR_KIND[kind]), len(hits),
                        chart_ticker(last.ticker)))
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda w: (-w.count, -date.fromisoformat(w.because.date).toordinal(),
                                      order[w.kind]))


# ------------------------------------------------------------- candidates

def _as_wait(c: Candidate, kind: str, reason: str, **facts) -> Candidate:
    return replace(c, enter=False, wait_reason=reason, stop=None, target=None, entry=None,
                   facts={**c.facts, "trade_kind": kind, "was_enter": c.enter, **facts})


def late_candidate(c: Candidate, cutoff: time = DEFAULT_CUTOFF) -> Candidate | None:
    """Any setup whose pause is after the cutoff (PT) becomes a wait: the window is closed."""
    at = _utc(c.pause_at).astimezone(PT)
    if at.time() <= cutoff:
        return None
    return _as_wait(c, "late", "after_cutoff", cutoff=cutoff.strftime("%H:%M"))


def countertrend_candidate(c: Candidate, bars: Sequence[Candle],
                           cutoff: time = DEFAULT_CUTOFF) -> Candidate | None:
    """A setup (inside the window) whose side fights the trend at the pause becomes a wait."""
    if _utc(c.pause_at).astimezone(PT).time() > cutoff:
        return None   # that one is a late drill; keep each drill about one thing
    t = trend_at(bars, c.pause_at)
    if not against(c.side, t):
        return None
    return _as_wait(c, "countertrend", "countertrend", trend=t)


def _runner(c: Candidate, risk: float) -> tuple[str, float] | None:
    for name, px in c.facts.get("next_levels") or []:
        if abs(px - c.target) >= RUNNER_MIN_R * risk - 1e-9:
            return str(name), float(px)
    return None


def first_target_candidate(c: Candidate, bars: Sequence[Candle], lookahead_min: int,
                           session_close: datetime) -> Candidate | None:
    """An LTP enter setup that reached its first target, paused on the bar that touched it.

    The new key: the level is the first target, enter = hold a runner in the same direction,
    stop at break-even (the fill), target = the next level at least RUNNER_MIN_R past it."""
    if c.detector != "ltp_pullback" or not c.enter or c.stop is None or c.target is None:
        return None
    until = min(_utc(c.pause_at) + timedelta(minutes=lookahead_min), session_close)
    out = play_out(bars, c.pause_at, until, c.side, c.stop, c.target, c.entry)
    if out.result != "target" or out.at is None or out.entry is None:
        return None
    fill = out.entry
    risk = abs(fill - c.stop)
    runner = _runner(c, risk)
    if runner is None:
        return None
    hit = datetime.fromisoformat(out.at.replace("Z", "+00:00"))
    pause = hit + timedelta(minutes=1)
    last = [b for b in bars if _utc(b.ts) < pause]
    if not last:
        return None
    px = last[-1].close
    long_ = c.side == "long"
    if not ((fill < px < runner[1]) if long_ else (runner[1] < px < fill)):
        return None   # the target bar reversed through break-even: nothing to hold
    return replace(
        c, pause_at=pause, level=round(c.target, 2), enter=True, wait_reason=None,
        stop=round(fill, 2), target=round(runner[1], 2), entry=None,
        facts={**c.facts, "trade_kind": "first_target", "first_target": round(c.target, 2),
               "first_target_at": out.at, "fill": round(fill, 2), "runner_name": runner[0]},
    )


def make_candidate(kind: str, c: Candidate, bars: Sequence[Candle], *, cutoff: time,
                   lookahead_min: int, session_close: datetime) -> Candidate | None:
    if kind == "late":
        return late_candidate(c, cutoff)
    if kind == "countertrend":
        return countertrend_candidate(c, bars, cutoff)
    if kind == "first_target":
        return first_target_candidate(c, bars, lookahead_min, session_close)
    return None


def preference(c: Candidate, want: Want) -> tuple:
    """Sort key (ascending = better): tempting setups (the raw setup was an enter) first, then
    the same ticker as your trade, then strength; ties by newest date, ticker, pause."""
    tempting = bool(c.facts.get("was_enter", c.enter))
    score = c.strength + 0.2 * tempting + 0.15 * (c.ticker == want.ticker)
    return (-round(score, 6), -date.fromisoformat(c.date).toordinal(), c.ticker,
            _utc(c.pause_at).isoformat())


# ------------------------------------------------------------ explanation

def _pt_label(ts: datetime) -> str:
    return _utc(ts).astimezone(PT).strftime("%I:%M %p PT").lstrip("0")


def _cite(refs: dict[str, str], step: str) -> str:
    return f" (rule: {refs[step]})" if refs.get(step) else ""


def explain(c: Candidate, key: AnswerKey, refs: dict[str, str]) -> str:
    f = c.facts
    kind = f.get("trade_kind")
    long_ = c.side == "long"
    head = f"**{c.ticker} on {c.date}, paused at {_pt_label(c.pause_at)}.**"
    if kind == "first_target":
        return "\n".join([
            f"{head} An LTP pullback {c.side} filled at ${f['fill']:.2f} and has just reached its "
            f"first target, ${f['first_target']:.2f} ({f.get('target_name', 'the next level')}).",
            "",
            f"- **Level:** the first target, ${key.level:.2f}.{_cite(refs, 'level')}",
            f"- **Hold or take it all:** hold a runner ({c.side}). The trend that paid the first "
            f"target is still in place, and the next level is at least {RUNNER_MIN_R:g}R further."
            f"{_cite(refs, 'enter')}",
            f"- **Stop ${key.stop:.2f}:** break-even, the fill. The runner can no longer turn "
            f"into a loss.{_cite(refs, 'stop')}",
            f"- **Target ${key.target:.2f}:** the next level, {f.get('runner_name', 'level')}."
            f"{_cite(refs, 'target')}",
        ])
    kind_word = "resistance" if f.get("kind") == "res" else "support"
    lvl = f"- **Level:** ${key.level:.2f} ({f.get('level_name') or kind_word}).{_cite(refs, 'level')}"
    if kind == "countertrend":
        up = f.get("trend") == "up"
        return "\n".join([
            head, "", lvl,
            f"- **Enter or wait:** wait. The trend is {'up' if up else 'down'}: EMA8 "
            f"{'above' if up else 'below'} EMA21 and price {'above' if up else 'below'} VWAP. "
            f"A {'long' if long_ else 'short'} here fights it: a countertrend trade. Wait for "
            f"the trend to turn, or look for the trade in the trend's direction."
            f"{_cite(refs, 'enter')}",
        ])
    if kind == "late":
        return "\n".join([
            head, "", lvl,
            f"- **Enter or wait:** wait. It is {_pt_label(c.pause_at)}, after your "
            f"{f.get('cutoff', '08:30')} PT cutoff. However clean the setup looks, the window "
            f"is closed.{_cite(refs, 'enter')}",
        ])
    return head


# ------------------------------------------------------------------ signals

def signals_from_days(days) -> list[TradeSignal]:
    """TradeSignals from desk ``DayData`` (rule results, ledger tags, excursion facts)."""
    from app.analytics.excursions import excursion_codes

    out: list[TradeSignal] = []
    for d in days:
        rr = d.rule_results.trades if d.rule_results else {}
        for lp, a in zip(d.loaded, d.result.positions):
            codes = {t.code for t in a.tags}
            tr = rr.get(lp.trade_id)
            if tr is not None:
                codes |= {r.code for r in tr.results if r.kept is False}
            codes |= set(excursion_codes(d.excursions.get(lp.trade_id)))
            out.append(TradeSignal(
                trade_id=lp.trade_id, date=d.result.day.isoformat(),
                ticker=lp.position.ticker,
                side={"C": "long", "P": "short"}.get(lp.position.type),
                entry_ts=a.fills[0].ts if a.fills else None,
                codes=frozenset(codes),
            ))
    return out


def load_signals(for_date: date, days: int = WINDOW_DAYS, *, rules=None, engine=None
                 ) -> list[TradeSignal]:
    """Signals for every real trade with a session date in [for_date - days, for_date].
    Excursions come from cached bars only (no provider call)."""
    from app.desk import days as desk_days
    from app.ingest.excursion_rollup import attach_excursions

    rules, _, dd = desk_days.patterns(for_date - timedelta(days=days), for_date, rules, engine)
    try:
        attach_excursions(dd, rules, None, engine, "cache")
    except Exception:   # no bars: exited_early stays unknown, the rest still counts
        pass
    return signals_from_days(dd)
