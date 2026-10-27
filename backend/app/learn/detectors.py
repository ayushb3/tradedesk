"""Pure setup detectors over one session of 1-minute candles.

Each detector scans bar by bar and only ever looks at bars up to the candidate's setup bar
(levels use pivots that were already confirmed by then), so a candidate is exactly what a
trader could have seen at the pause. The pause sits right after the setup bar.

A candidate carries the mechanical facts the answer key needs: the level, the side, whether the
entry condition held by the pause, the setup candle extreme (for the stop) and the next level
(for the target). Mapping those to course rules happens in build.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Sequence

from app.models.market import Candle

# Knobs, as fractions of price (0.0004 = 4 bp). Tuned for 1m bars on liquid ETFs.
PIVOT_K = 3            # bars each side that must be lower/higher for a pivot
TOUCH_TOL = 0.0004     # pivots this close belong to one level
MIN_TOUCH_GAP = 6      # bars between two touches of a level
APPROACH = 0.0006      # "at the level" band
DEPART = 0.0015        # price must have left the level by at least this
THROUGH = 0.0008       # a push this far past the level is a break, not a touch
BREAK = 0.0004         # close beyond the level by this = break
EXTEND = 0.0012        # break must travel this far before the retest
HOLD = 0.0006          # a retest may undercut the level by at most this
STOP_BUFFER = 0.02     # "just past" the setup candle extreme, in dollars
MIN_RR = 1.5           # the next level must pay at least this multiple of the risk
MIN_RISK = 0.0004      # risk (level to stop) as a fraction of price
MAX_RISK = 0.006
OR_BARS = 15           # opening-range window
EARLIEST = 25          # no setup before this bar index
LEAVE_AFTER = 45       # keep this many bars after the pause so the result can play out


@dataclass
class Candidate:
    detector: str
    ticker: str
    date: str                 # session date (YYYY-MM-DD, ET)
    pause_at: datetime        # open time of the bar after the setup bar (UTC)
    level: float
    side: str                 # "long" | "short"
    strength: float           # 0..1
    setup_idx: int = -1
    enter: bool = False       # did the entry condition hold by the pause
    stop: float | None = None
    target: float | None = None
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pause_at"] = self.pause_at.isoformat().replace("+00:00", "Z")
        return d


@dataclass
class Level:
    kind: str                 # "res" | "sup"
    price: float
    touches: list[int]        # bar indexes of the touches (pivots)
    source: str = "pivot"     # "pivot" | "or"


# --------------------------------------------------------------------- helpers

def _pivots(c: Sequence[Candle], k: int = PIVOT_K) -> list[tuple[int, str, float]]:
    """(index, 'high'|'low', price). A pivot at i is only knowable at bar i + k."""
    out = []
    for i in range(k, len(c) - k):
        hs = [b.high for b in c[i - k:i + k + 1]]
        ls_ = [b.low for b in c[i - k:i + k + 1]]
        if c[i].high >= max(hs) and c[i].high > max(b.high for b in c[i - k:i]):
            out.append((i, "high", c[i].high))
        if c[i].low <= min(ls_) and c[i].low < min(b.low for b in c[i - k:i]):
            out.append((i, "low", c[i].low))
    return out


def _levels_at(c: Sequence[Candle], pivots, j: int) -> list[Level]:
    """Levels knowable at bar j: pivot clusters (confirmed by j) plus the opening range."""
    levels: list[Level] = []
    for kind, label in (("high", "res"), ("low", "sup")):
        clusters: list[Level] = []
        for i, k_, p in pivots:
            if k_ != kind or i + PIVOT_K > j:
                continue
            for lv in clusters:
                if abs(p - lv.price) / lv.price <= TOUCH_TOL:
                    if i - lv.touches[-1] >= MIN_TOUCH_GAP:
                        lv.touches.append(i)
                        lv.price = sum(c[t].high if kind == "high" else c[t].low
                                       for t in lv.touches) / len(lv.touches)
                    break
            else:
                clusters.append(Level(label, p, [i]))
        levels.extend(clusters)
    if j >= OR_BARS:
        orh = max(range(OR_BARS), key=lambda i: c[i].high)
        orl = min(range(OR_BARS), key=lambda i: c[i].low)
        levels.append(Level("res", c[orh].high, [orh], "or"))
        levels.append(Level("sup", c[orl].low, [orl], "or"))
    return levels


def _next_level(c, levels: list[Level], pivots, j: int, side: str, entry: float,
                risk: float) -> float | None:
    """Nearest earlier high/low (or the day's extreme) beyond entry paying >= MIN_RR."""
    need = MIN_RR * risk
    cands = [p for i, k_, p in pivots if i + PIVOT_K <= j and
             ((side == "long" and k_ == "high" and p >= entry + need) or
              (side == "short" and k_ == "low" and p <= entry - need))]
    day_hi = max(b.high for b in c[:j + 1])
    day_lo = min(b.low for b in c[:j + 1])
    if side == "long" and day_hi >= entry + need:
        cands.append(day_hi)
    if side == "short" and day_lo <= entry - need:
        cands.append(day_lo)
    if not cands:
        return None
    return round(min(cands) if side == "long" else max(cands), 2)


def _pause(c: Sequence[Candle], j: int) -> datetime:
    return c[j].ts + timedelta(minutes=1)


def _risk_ok(entry: float, stop: float) -> bool:
    r = abs(entry - stop) / entry
    return MIN_RISK <= r <= MAX_RISK


def _stop_for(side: str, bar: Candle, level: float) -> float:
    """Just past the setup candle's extreme (and never on the wrong side of the level)."""
    if side == "long":
        return round(min(bar.low, level) - STOP_BUFFER, 2)
    return round(max(bar.high, level) + STOP_BUFFER, 2)


def _build(detector: str, ticker: str, day: str, c, j: int, lv: Level, side: str,
           enter: bool, strength: float, pivots, levels, facts: dict) -> Candidate | None:
    """Entry is the setup bar's close (the price at the pause); the stop sits just past the
    setup candle's extreme; the target is the next level paying >= MIN_RR."""
    entry = c[j].close
    stop = _stop_for(side, c[j], lv.price)
    target = None
    if enter:
        if not _risk_ok(entry, stop):
            return None
        target = _next_level(c, levels, pivots, j, side, entry, abs(entry - stop))
        if target is None:
            return None  # an enter drill needs a rule-shaped target
    return Candidate(
        detector=detector, ticker=ticker, date=day, pause_at=_pause(c, j),
        level=round(lv.price, 2), side=side, strength=round(min(max(strength, 0.0), 1.0), 3),
        setup_idx=j, enter=enter, stop=stop if enter else None, target=target,
        facts={**facts, "touches": len(lv.touches), "level_source": lv.source,
               "setup_low": c[j].low, "setup_high": c[j].high},
    )


def _scan(c: Sequence[Candle], fn: Callable[[int, list[Level], list], list[Candidate]]) -> list[Candidate]:
    piv = _pivots(c)
    out: list[Candidate] = []
    for j in range(EARLIEST, len(c) - LEAVE_AFTER):
        out.extend(fn(j, _levels_at(c, piv, j), piv))
    return out


# --------------------------------------------------------------- level_touches

def level_touches(candles: Sequence[Candle], ticker: str, day: str) -> list[Candidate]:
    """A level touched >= 2 times, price left it, and the first bar back at the level."""
    c = list(candles)

    def at(j: int, levels: list[Level], piv) -> list[Candidate]:
        res = []
        b = c[j]
        for lv in levels:
            if lv.source != "pivot" or len(lv.touches) < 2:
                continue
            t = lv.touches[-1]
            if j - t < MIN_TOUCH_GAP:
                continue
            L = lv.price
            if lv.kind == "res":
                left = min(x.low for x in c[t + 1:j]) <= L * (1 - DEPART)
                quiet = all(x.high < L * (1 - APPROACH) for x in c[t + 3:j])
                here = L * (1 - APPROACH) <= b.high <= L * (1 + THROUGH)
                side, hold = "short", b.close < L and b.close < b.open
                depth = (L - min(x.low for x in c[t + 1:j])) / L
            else:
                left = max(x.high for x in c[t + 1:j]) >= L * (1 + DEPART)
                quiet = all(x.low > L * (1 + APPROACH) for x in c[t + 3:j])
                here = L * (1 - THROUGH) <= b.low <= L * (1 + APPROACH)
                side, hold = "long", b.close > L and b.close > b.open
                depth = (max(x.high for x in c[t + 1:j]) - L) / L
            if not (left and quiet and here):
                continue
            strength = (0.40 + 0.15 * min(len(lv.touches) - 2, 2)
                        + 0.30 * min(depth / (3 * DEPART), 1.0) + (0.10 if hold else 0.0))
            cand = _build("level_touches", ticker, day, c, j, lv, side, hold, strength,
                          piv, levels, {"rejection": hold, "kind": lv.kind,
                                        "first_touch_idx": lv.touches[0], "last_touch_idx": t})
            if cand:
                res.append(cand)
        return res

    return _dedupe(_scan(c, at))


# ---------------------------------------------------------------- break_retest

def break_retest(candles: Sequence[Candle], ticker: str, day: str) -> list[Candidate]:
    """A level breaks, price travels away, comes back to retest it and holds."""
    c = list(candles)

    def at(j: int, levels: list[Level], piv) -> list[Candidate]:
        res = []
        b = c[j]
        for lv in levels:
            if len(lv.touches) < 2 and lv.source != "or":
                continue
            L, t = lv.price, lv.touches[-1]
            up = lv.kind == "res"  # resistance breaking up -> long retest
            start = max(t + 1, OR_BARS if lv.source == "or" else 0)
            brk = next((i for i in range(start, j)
                        if (c[i].close > L * (1 + BREAK) if up else c[i].close < L * (1 - BREAK))), None)
            if brk is None or not 3 <= j - brk <= 30:
                continue
            seg = c[brk + 1:j]
            if up:
                travelled = max(x.high for x in c[brk:j]) >= L * (1 + EXTEND)
                stayed = all(x.close >= L for x in seg)
                clean = all(x.low > L * (1 + APPROACH) for x in c[brk + 2:j])
                here = L * (1 - HOLD) <= b.low <= L * (1 + APPROACH)
                enter = b.close >= L and b.close > b.open
                side, held = "long", b.close >= L
                ext = (max(x.high for x in c[brk:j]) - L) / L
            else:
                travelled = min(x.low for x in c[brk:j]) <= L * (1 - EXTEND)
                stayed = all(x.close <= L for x in seg)
                clean = all(x.high < L * (1 - APPROACH) for x in c[brk + 2:j])
                here = L * (1 - APPROACH) <= b.high <= L * (1 + HOLD)
                enter = b.close <= L and b.close < b.open
                side, held = "short", b.close <= L
                ext = (L - min(x.low for x in c[brk:j])) / L
            if not (travelled and stayed and clean and here and held):
                continue
            strength = (0.45 + 0.15 * min(len(lv.touches) - 1, 2)
                        + 0.25 * min(ext / (3 * EXTEND), 1.0) + (0.10 if enter else 0.0))
            cand = _build("break_retest", ticker, day, c, j, lv, side, enter, strength, piv,
                          levels, {"break_idx": brk, "bars_since_break": j - brk,
                                   "confirmation": enter, "kind": lv.kind})
            if cand:
                res.append(cand)
        return res

    return _dedupe(_scan(c, at))


# ------------------------------------------------------- patience_candle_at_level

def patience_candle_at_level(candles: Sequence[Candle], ticker: str, day: str) -> list[Candidate]:
    """After a pullback to a level, a small-range candle sits at it."""
    c = list(candles)

    def at(j: int, levels: list[Level], piv) -> list[Candidate]:
        res = []
        b = c[j]
        prev = c[j - 8:j]
        avg_range = sum(x.high - x.low for x in prev) / len(prev)
        rng = b.high - b.low
        if avg_range <= 0 or rng > 0.6 * avg_range:
            return res
        inside = b.high <= c[j - 1].high and b.low >= c[j - 1].low
        for lv in levels:
            if lv.touches[-1] > j - 8:
                continue
            L = lv.price
            down = lv.kind == "sup"  # pulled back DOWN to support -> long
            ref = c[j - 4].close
            if down:
                pulled = ref > L * (1 + DEPART) and c[j - 1].close < ref and \
                    (ref - b.low) / L >= 0.0012
                here = L * (1 - HOLD) <= b.low <= L * (1 + APPROACH)
                hold, side = b.close >= L, "long"
            else:
                pulled = ref < L * (1 - DEPART) and c[j - 1].close > ref and \
                    (b.high - ref) / L >= 0.0012
                here = L * (1 - APPROACH) <= b.high <= L * (1 + HOLD)
                hold, side = b.close <= L, "short"
            if not (pulled and here):
                continue
            enter = hold and inside
            strength = (0.40 + 0.15 * min(len(lv.touches) - 1, 2)
                        + 0.25 * min(1.0, 1.0 - rng / (0.6 * avg_range) + 0.4)
                        + (0.10 if inside else 0.0) + (0.10 if enter else 0.0))
            cand = _build("patience_candle_at_level", ticker, day, c, j, lv, side, enter,
                          strength, piv, levels,
                          {"inside_bar": inside, "holds_level": hold,
                           "range_vs_avg": round(rng / avg_range, 2), "kind": lv.kind})
            if cand:
                res.append(cand)
        return res

    return _dedupe(_scan(c, at))


# ----------------------------------------------------------------------- shared

def _dedupe(cands: list[Candidate], gap: int = 8) -> list[Candidate]:
    """One candidate per level per stretch of bars: keep the strongest of near neighbours."""
    cands = sorted(cands, key=lambda x: x.setup_idx)
    out: list[Candidate] = []
    for cand in cands:
        twin = next((o for o in out if abs(o.level - cand.level) / cand.level <= TOUCH_TOL
                     and cand.setup_idx - o.setup_idx <= gap), None)
        if twin is None:
            out.append(cand)
        elif cand.strength > twin.strength:
            out[out.index(twin)] = cand
    return out


DETECTORS: dict[str, Callable[[Sequence[Candle], str, str], list[Candidate]]] = {
    "level_touches": level_touches,
    "break_retest": break_retest,
    "patience_candle_at_level": patience_candle_at_level,
}


def detect_all(candles: Sequence[Candle], ticker: str, day: str) -> list[Candidate]:
    out: list[Candidate] = []
    for fn in DETECTORS.values():
        out.extend(fn(candles, ticker, day))
    return out
