"""The course's LTP entry (levels, trend, patience candle) as a drill detector.

The bars are the ones the drill chart shows on "auto": 1m bars rolled up KCU-style, 2m for the
first 30 minutes, 5m for the next hour, then 10m (strategy-setups: chart timeframes). On those:

- trend: EMA8 above EMA21 and the close on the right side of VWAP, and a new high (low) of day
  within the last 6 bars (the A->B leg, at least LEG_MIN of price);
- pullback: B->C retraces less than half the leg (a 0.5 Fibonacci touch takes the setup off);
- patience candle: the latest bar is contained in the trade direction (no new high for a long,
  no new low for a short) and smaller than PC_MAX x the recent average range;
- confluence: the pullback extreme sits within CONF_TOL of a level the chart can show (VWAP,
  EMA8, opening range, today's closed hourly highs/lows, premarket and prior-day levels).

Entry is a stop order one cent past the candle (``Candidate.entry``), the stop one cent past
its other side, the target the nearest level paying at least MIN_RR. The pause is the candle's
close. A pullback that reached the 0.5 Fibonacci with a contained candle at a level is a wait.

Replayed on 25 SPY/QQQ sessions (2026-09-29..10-30), 22 setups filled at +0.44R on average; about
half the candles broke the wrong way first and never filled. That is why the builder still
gates every enter key on its outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from app.models.market import Candle

from .detectors import Candidate

ET = ZoneInfo("America/New_York")

LEG_MIN = 0.0025      # the A->B leg must be at least this share of price
LEG_LOOKBACK = 12     # bars before B searched for A
B_RECENT = 6          # the high/low of day must be at most this many bars old
FIB_OFF = 0.5         # a pullback reaching this share of the leg takes the setup off
FIB_MIN = 0.1         # less than this is not a pullback yet
PC_MAX = 0.8          # patience candle range vs the 6-bar average range
CONF_TOL = 0.0010     # pullback extreme within this share of price of a level = confluence
MIN_RR = 2.0          # the course wants a large reward vs risk (roughly 1:3; 2R is the floor)
MAX_RR = 8.0
MIN_RISK = 0.0003     # candle risk below this share of price is noise
TICK = 0.01
LAST_SETUP = time(14, 55)   # no new trades after 3 pm ET (strategy-setups: time windows)
GAP = 3               # bars between two setups of the same session


@dataclass
class SessionContext:
    """Levels known before the open: premarket and prior-day (safe to show from the first bar)."""
    levels: dict[str, float] = field(default_factory=dict)


@dataclass
class _Bar:
    ts: datetime          # open time (UTC)
    minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def end(self) -> datetime:
        return self.ts + timedelta(minutes=self.minutes)


def kcu_minutes(minutes_since_open: float) -> int:
    """Bar size on the drill chart's auto setting (frontend kcuTimeframe)."""
    return 2 if minutes_since_open < 30 else 5 if minutes_since_open < 90 else 10


def auto_bars(c: Sequence[Candle], open_utc: datetime) -> list[_Bar]:
    """1m bars rolled up into the auto chart's bars, buckets aligned to the open."""
    out: list[_Bar] = []
    key = None
    for b in c:
        m = (b.ts - open_utc).total_seconds() / 60
        tf = kcu_minutes(m)
        k = (tf, int(m // tf))
        if out and k == key:
            x = out[-1]
            x.high, x.low, x.close = max(x.high, b.high), min(x.low, b.low), b.close
            x.volume += b.volume
        else:
            start = open_utc + timedelta(minutes=k[1] * tf)
            out.append(_Bar(start, tf, b.open, b.high, b.low, b.close, b.volume))
            key = k
    return out


def _ema(vals: list[float], n: int) -> list[float]:
    k, e, out = 2 / (n + 1), None, []
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def _vwaps(c: Sequence[Candle], bars: list[_Bar]) -> list[float]:
    """Session VWAP as of each bar's close (typical price, volume weighted, from the open)."""
    out, pv, vv, i = [], 0.0, 0.0, 0
    for b in bars:
        while i < len(c) and c[i].ts < b.end:
            x = c[i]
            pv += (x.high + x.low + x.close) / 3 * x.volume
            vv += x.volume
            i += 1
        out.append(pv / vv if vv else b.close)
    return out


def _hourly(c: Sequence[Candle], open_utc: datetime, by: datetime) -> dict[str, float]:
    """High and low of each hourly bar (open-aligned) closed by ``by`` (the chart's 1h levels)."""
    out: dict[str, float] = {}
    h = 0
    while open_utc + timedelta(hours=h + 1) <= by:
        lo, hi = open_utc + timedelta(hours=h), open_utc + timedelta(hours=h + 1)
        seg = [b for b in c if lo <= b.ts < hi]
        if seg:
            label = (lo.astimezone(ET) - timedelta(hours=3)).strftime("%H:%M")   # PT label
            out[f"1h H {label}"] = max(b.high for b in seg)
            out[f"1h L {label}"] = min(b.low for b in seg)
        h += 1
    return out


def ltp_pullback(candles: Sequence[Candle], ticker: str, day: str,
                 ctx: SessionContext | None = None) -> list[Candidate]:
    c = list(candles)
    if len(c) < 60:
        return []
    open_utc = c[0].ts.replace(second=0, microsecond=0)
    bars = auto_bars(c, open_utc)
    closes = [b.close for b in bars]
    e8, e21, vw = _ema(closes, 8), _ema(closes, 21), _vwaps(c, bars)
    orb = [b for b in c if b.ts < open_utc + timedelta(minutes=15)]
    or_hi, or_lo = max(b.high for b in orb), min(b.low for b in orb)
    static = dict(ctx.levels) if ctx else {}

    out: list[Candidate] = []
    last = -99
    for j in range(8, len(bars)):          # 8 two-minute bars = after the 15m opening range
        pc, prev = bars[j], bars[j - 1]
        if pc.end.astimezone(ET).time() > LAST_SETUP:
            break
        if j - last < GAP:
            continue
        levels = {**static, "ORB high": or_hi, "ORB low": or_lo, "VWAP": vw[j], "EMA8": e8[j],
                  **_hourly(c, open_utc, pc.end)}
        for side in ("long", "short"):
            cand = _setup(c, bars, j, side, e8, e21, vw, levels, ticker, day)
            if cand:
                out.append(cand)
                last = j
                break
    return out


def _setup(c, bars: list[_Bar], j: int, side: str, e8, e21, vw, levels: dict[str, float],
           ticker: str, day: str) -> Candidate | None:
    long_ = side == "long"
    pc, prev = bars[j], bars[j - 1]
    if long_ and not (e8[j] > e21[j] and pc.close > vw[j]):
        return None
    if not long_ and not (e8[j] < e21[j] and pc.close < vw[j]):
        return None
    b_i = (max(range(j), key=lambda i: bars[i].high) if long_
           else min(range(j), key=lambda i: bars[i].low))
    if not 1 <= j - b_i <= B_RECENT:
        return None
    b_px = bars[b_i].high if long_ else bars[b_i].low
    leg_bars = bars[max(0, b_i - LEG_LOOKBACK):b_i + 1]
    a_px = min(b.low for b in leg_bars) if long_ else max(b.high for b in leg_bars)
    leg = abs(b_px - a_px)
    if leg / b_px < LEG_MIN:
        return None
    pull = bars[b_i + 1:j + 1]
    ext = min(b.low for b in pull) if long_ else max(b.high for b in pull)
    retr = abs(b_px - ext) / leg
    if not FIB_MIN <= retr < 0.85:     # deeper than 0.85 is a reversal, not a pullback
        return None
    contained = pc.high <= prev.high if long_ else pc.low >= prev.low
    avg = sum(b.high - b.low for b in bars[j - 6:j]) / 6
    small = (pc.high - pc.low) <= PC_MAX * avg
    if not (contained and small):
        return None
    near = sorted(((n, p) for n, p in levels.items() if abs(ext - p) <= CONF_TOL * pc.close),
                  key=lambda x: abs(ext - x[1]))
    if not near:
        return None
    lvl_name, lvl = near[0]
    pause = pc.end
    facts = {"kind": "sup" if long_ else "res", "level_name": lvl_name,
             "confluence": [n for n, _ in near], "retracement": round(retr, 2),
             "leg_from": round(a_px, 2), "leg_to": round(b_px, 2), "bar_minutes": pc.minutes,
             "candle_time": pc.ts.isoformat(), "candle_high": round(pc.high, 2),
             "candle_low": round(pc.low, 2), "vwap": round(vw[j], 2), "ema8": round(e8[j], 2)}
    strength = 0.55 + 0.1 * min(len(near) - 1, 2) + 0.15 * (1 - retr / FIB_OFF if retr < FIB_OFF else 0)
    base = dict(detector="ltp_pullback", ticker=ticker, date=day, pause_at=pause,
                level=round(lvl, 2), side=side, setup_idx=j)
    if retr >= FIB_OFF:
        return Candidate(**base, strength=round(min(strength, 1.0) - 0.1, 3), enter=False,
                         wait_reason="pullback_past_half", facts=facts)
    entry = round(pc.high + TICK, 2) if long_ else round(pc.low - TICK, 2)
    stop = round(pc.low - TICK, 2) if long_ else round(pc.high + TICK, 2)
    risk = abs(entry - stop)
    if risk / entry < MIN_RISK:
        return None
    pays = [p for p in [*levels.values(), b_px]
            if (p - entry if long_ else entry - p) >= MIN_RR * risk]
    if not pays:
        return None
    target = round(min(pays) if long_ else max(pays), 2)
    if abs(target - entry) > MAX_RR * risk:
        return None
    tname = next((n for n, p in levels.items() if round(p, 2) == target),
                 "high of day" if long_ else "low of day")
    # the levels past the target, nearest first: where a runner would aim (drills from trades)
    beyond = sorted({(n, round(p, 2)) for n, p in [*levels.items(), ("high of day" if long_
                     else "low of day", b_px)] if (p > target if long_ else p < target)},
                    key=lambda x: (abs(x[1] - target), x[0]))
    facts.update(target_name=tname, rr=round(abs(target - entry) / risk, 2), entry=entry,
                 next_levels=[[n, p] for n, p in beyond[:4]])
    return Candidate(**base, strength=round(min(strength + 0.1, 1.0), 3), enter=True,
                     entry=entry, stop=stop, target=target, facts=facts)
