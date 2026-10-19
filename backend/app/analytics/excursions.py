"""Trade excursions: MAE/MFE over the hold, the stop line, and the post-exit path.

Pure functions over premium bars (no DB, no network, no provider), so the trade
review, the MCP read tool and the multi-trade rollup (#52) all share one set of
definitions.

Definitions (a long option position, the only kind the desk ledger models):

* Basis is the position's **average cost** per contract (``avg_in``) and total
  contracts bought (``qty``). Dollars = price move x ``multiplier`` x ``qty``.
* **R** = dollars / ``risk``, where ``risk`` is the ledger's own dollars-at-risk
  (``PositionAnalysis.risk``: the written max loss, else cost x
  ``default_risk_pct``). This module never invents a second risk definition; the
  caller passes the ledger's number in.
* **MAE** is the worst the position was, as a signed value (<= 0); **MFE** the
  best (>= 0). Both use bar lows/highs between the first fill and the last exit.
* **Stop line** is the premium at which the position has lost exactly ``risk``:
  ``avg_in - risk / (multiplier * qty)``. It is None when that price would be at
  or below zero (the risk is larger than the whole premium, so there is no
  premium level to cross).
* **Post-exit path** looks at bars starting at the final exit and reports the
  best and worst premium in the next 15/30/60 minutes, in R on the same cost
  basis. It is informational only; there is deliberately no "money left on the
  table" figure here or anywhere downstream.

Bars are keyed by their open time (``Candle.ts``, UTC). A bar belongs to the hold
if it overlaps ``[open_ts, close_ts]``; a bar belongs to a post-exit window if it
opens at or after ``close_ts`` and before ``close_ts + window``.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.market import Candle

CONTRACT_MULT = 100
POST_EXIT_WINDOWS_MIN: tuple[int, ...] = (15, 30, 60)
SHORT_HOLD_BARS = 2  # a closed hold touching this many bars or fewer is a short hold


def _utc(ts: datetime) -> datetime:
    return (ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)).astimezone(
        timezone.utc
    )


def _tf_minutes(bars: list[Candle]) -> int:
    tf = bars[0].timeframe if bars else "1m"
    try:
        return max(1, int(tf.rstrip("m")))
    except ValueError:
        return 1


def _to_r(dollars: float, risk: float | None) -> float | None:
    return dollars / risk if risk else None


@dataclass(frozen=True)
class HoldExcursion:
    mae_per_contract: float  # worst premium move vs avg cost, price points (<= 0)
    mfe_per_contract: float  # best premium move vs avg cost, price points (>= 0)
    mae_usd: float  # signed, <= 0
    mfe_usd: float  # >= 0
    mae_r: float | None
    mfe_r: float | None
    mae_ts: datetime | None  # bar open time of the lowest low
    mfe_ts: datetime | None
    drawdown_minutes: int  # bars (x timeframe) closing below avg cost during the hold
    hold_bars: int
    crossed_stop: bool
    crossed_stop_at: datetime | None  # first bar whose low reached the stop line
    short_hold: bool = False  # hold inside <= 2 bars: measured from the fills alone
    short_hold_bars: int = 0  # how many bars the hold touched (N in 'held under N min')


@dataclass(frozen=True)
class PostExitWindow:
    minutes: int
    best_r: float | None  # None when no bars fall in the window
    worst_r: float | None
    best_per_contract: float | None  # price points vs avg cost
    worst_per_contract: float | None
    bars: int  # bars found in the window
    complete: bool  # bars cover the whole window (False near the session end)


@dataclass(frozen=True)
class Excursions:
    avg_in: float
    qty: int
    risk: float | None
    stop_price: float | None
    hold: HoldExcursion | None
    post_exit: list[PostExitWindow] = field(default_factory=list)
    wicks: WickCheck | None = None  # set when fills were given (data-quality check)
    raw_hold: HoldExcursion | None = None  # same bounds, wicks NOT trimmed (only when suspect)


def stop_price(avg_in: float, qty: int, risk: float | None, mult: int = CONTRACT_MULT) -> float | None:
    """Premium level at which the position has lost ``risk`` dollars, or None."""
    if not risk or qty <= 0 or risk <= 0:
        return None
    level = avg_in - risk / (mult * qty)
    return round(level, 4) if level > 0 else None


Fill = tuple[datetime, float]  # (fill time, fill price)


def bounded_hold_bars(
    bars: list[Candle], *, fills: list[Fill], open_ts: datetime, close_ts: datetime
) -> tuple[list[Candle], int]:
    """The bars that describe the hold, edge minutes bounded by the fills.

    A bar fully inside ``[open_ts, close_ts]`` counts whole. A bar that only partly
    overlaps the hold (the one holding the opening fill, which opened before it, and the
    one holding the final exit, which closes after it) also holds trading from OUTSIDE the
    hold, so its high/low cannot be used. Each such edge bar is replaced by the fill
    price(s) that fall inside it (open = first, close = last, high/low = their extremes).

    Returns ``(bars, n_touched)``: the bounded bars in time order, and how many bars the
    hold touched at all (1 = inside one minute).
    """
    if not bars:
        return [], 0
    o, c = _utc(open_ts), _utc(close_ts)
    tf = timedelta(minutes=_tf_minutes(bars))
    out: list[Candle] = []
    touched = 0
    for b in sorted(bars, key=lambda x: _utc(x.ts)):
        t = _utc(b.ts)
        if not (t + tf > o and (t < c or t <= o == c)):
            continue
        touched += 1
        if t >= o and t + tf <= c:
            out.append(b)
            continue
        px = [p for ts, p in sorted(fills, key=lambda f: _utc(f[0])) if t <= _utc(ts) < t + tf]
        if not px:
            continue
        out.append(
            b.model_copy(
                update={"open": px[0], "close": px[-1], "high": max(px), "low": min(px)}
            )
        )
    return out, touched


def hold_excursion(
    bars: list[Candle],
    *,
    avg_in: float,
    qty: int,
    risk: float | None,
    open_ts: datetime,
    close_ts: datetime,
    mult: int = CONTRACT_MULT,
    fills: list[Fill] | None = None,
    closed: bool = True,
) -> HoldExcursion | None:
    """MAE/MFE, time in drawdown and stop crossing over ``[open_ts, close_ts]``.

    With ``fills`` (the position's ``(ts, px)`` fills) the hold is measured on
    ``bounded_hold_bars``: only bars fully inside the hold contribute their high/low, the
    partly-overlapping edge minutes count as just their fill prices, so trading before the
    entry or after the exit never becomes MFE/MAE or a stop crossing. A closed hold that
    touches at most two bars is ``short_hold``: 1-min bars cannot resolve its path, so its
    numbers come from the fills alone. Without ``fills`` every overlapping bar counts whole
    (legacy behaviour). Returns None when no bar overlaps the hold.
    """
    if qty <= 0 or not bars:
        return None
    o, c = _utc(open_ts), _utc(close_ts)
    tf = timedelta(minutes=_tf_minutes(bars))
    touched = 0
    if fills:
        held, touched = bounded_hold_bars(bars, fills=fills, open_ts=o, close_ts=c)
    else:
        held = sorted(
            (b for b in bars if _utc(b.ts) + tf > o and _utc(b.ts) <= c), key=lambda b: _utc(b.ts)
        )
    if not held:
        return None

    low_bar = min(held, key=lambda b: b.low)
    high_bar = max(held, key=lambda b: b.high)
    mae_pc = min(0.0, low_bar.low - avg_in)
    mfe_pc = max(0.0, high_bar.high - avg_in)
    scale = mult * qty
    mae_usd, mfe_usd = mae_pc * scale, mfe_pc * scale

    stop = stop_price(avg_in, qty, risk, mult)
    crossed_at = None
    if stop is not None:
        hit = next((b for b in held if b.low <= stop), None)
        crossed_at = _utc(hit.ts) if hit else None

    underwater = sum(1 for b in held if b.close < avg_in)
    return HoldExcursion(
        mae_per_contract=round(mae_pc, 4),
        mfe_per_contract=round(mfe_pc, 4),
        mae_usd=round(mae_usd, 2),
        mfe_usd=round(mfe_usd, 2),
        mae_r=_round(_to_r(mae_usd, risk)),
        mfe_r=_round(_to_r(mfe_usd, risk)),
        mae_ts=_utc(low_bar.ts) if mae_pc < 0 else None,
        mfe_ts=_utc(high_bar.ts) if mfe_pc > 0 else None,
        drawdown_minutes=underwater * (tf.seconds // 60),
        hold_bars=len(held),
        crossed_stop=crossed_at is not None,
        crossed_stop_at=crossed_at,
        short_hold=bool(fills) and closed and 0 < touched <= SHORT_HOLD_BARS,
        short_hold_bars=touched if fills else 0,
    )


def post_exit_path(
    bars: list[Candle],
    *,
    avg_in: float,
    qty: int,
    risk: float | None,
    exit_ts: datetime,
    windows: tuple[int, ...] = POST_EXIT_WINDOWS_MIN,
    mult: int = CONTRACT_MULT,
) -> list[PostExitWindow]:
    """Best/worst premium, in R, within each window after the final exit.

    Windows are cumulative from the exit (15 min covers the first 15 minutes, 30
    the first 30, ...). A window with no bars reports None for best/worst.
    """
    e = _utc(exit_ts)
    after = sorted((b for b in bars if _utc(b.ts) >= e), key=lambda b: _utc(b.ts))
    tf = timedelta(minutes=_tf_minutes(bars))
    scale = mult * qty
    out: list[PostExitWindow] = []
    for w in windows:
        end = e + timedelta(minutes=w)
        inside = [b for b in after if _utc(b.ts) < end]
        if not inside or qty <= 0:
            out.append(PostExitWindow(w, None, None, None, None, len(inside), False))
            continue
        best_pc = max(b.high for b in inside) - avg_in
        worst_pc = min(b.low for b in inside) - avg_in
        covered_to = _utc(inside[-1].ts) + tf
        out.append(
            PostExitWindow(
                minutes=w,
                best_r=_round(_to_r(best_pc * scale, risk)),
                worst_r=_round(_to_r(worst_pc * scale, risk)),
                best_per_contract=round(best_pc, 4),
                worst_per_contract=round(worst_pc, 4),
                bars=len(inside),
                complete=covered_to >= end,
            )
        )
    return out


# ----------------------------------------------------------------------------
# Data quality: suspect wicks on thin contracts.
#
# 1-min bars on a thin 0DTE contract can print a single stray tick that is far
# from anything the trader actually traded. MAE/MFE read from bar lows/highs, so
# one such wick can make a 2-minute hold look like it went through half its
# premium. The check compares bar extremes during the hold with the hold's own
# fill prices.
#
# RULE (documented, one place). Only bars fully inside the hold are checked (the edge
# minutes are bounded by the fills, see ``bounded_hold_bars``). Let ``lo``/``hi`` be the lowest/highest fill price
# of the position and ``tol = max(WICK_MIN_ABS, WICK_REL * avg_in)``. A hold bar is a
# suspect LOW wick when
#     bar.low < lo - tol                      (strictly beyond the fills by more than tol)
#     and min(bar.open, bar.close) >= lo - tol / 2     (the body stayed near the fills)
# and a suspect HIGH wick when
#     bar.high > hi + tol
#     and max(bar.open, bar.close) <= hi + tol / 2.
# A bar whose body also left the fills is a real move, not a wick, and is kept.
# Landing exactly on ``lo - tol`` / ``hi + tol`` is not suspect (the boundary is
# "more than"). When any bar is suspect the review trims those bars to their body
# extreme (low -> min(open, close), high -> max(open, close)) before computing
# MAE/MFE and the stop crossing, and says so (``clamped``).

WICK_REL = 0.20  # of the average cost
WICK_MIN_ABS = 0.05  # price points, floor for very cheap contracts
_WICK_EPS = 1e-9


@dataclass(frozen=True)
class SuspectWick:
    ts: datetime  # bar open time
    side: str  # "low" | "high"
    raw: float  # the wick extreme as printed
    trimmed: float  # the body extreme used instead


@dataclass(frozen=True)
class WickCheck:
    suspect: list[SuspectWick]
    tolerance: float
    bars: list[Candle]  # the input bars with suspect wicks trimmed (same order)

    @property
    def quality(self) -> str:
        return "suspect_wicks" if self.suspect else "ok"


def check_hold_wicks(
    bars: list[Candle],
    *,
    fill_prices: list[float],
    avg_in: float,
    open_ts: datetime,
    close_ts: datetime | None,
) -> WickCheck:
    """Flag (and trim) wicks during the hold that sit far beyond the fills. See RULE above.

    Only bars fully inside ``[open_ts, close_ts]`` are checked (``close_ts`` None = to the
    end). The partly-overlapping edge minutes are bounded by the fills elsewhere
    (``bounded_hold_bars``) and bars outside the hold are returned untouched.
    """
    tol = max(WICK_MIN_ABS, WICK_REL * avg_in)
    if not bars or not fill_prices:
        return WickCheck([], tol, list(bars))
    lo, hi = min(fill_prices), max(fill_prices)
    tf = timedelta(minutes=_tf_minutes(bars))
    o = _utc(open_ts)
    c = _utc(close_ts) if close_ts is not None else None
    suspect: list[SuspectWick] = []
    out: list[Candle] = []
    for b in bars:
        t = _utc(b.ts)
        if not (t >= o and (c is None or t + tf <= c)):
            out.append(b)
            continue
        body_lo, body_hi = min(b.open, b.close), max(b.open, b.close)
        new_low, new_high = b.low, b.high
        if b.low < lo - tol - _WICK_EPS and body_lo >= lo - tol / 2 - _WICK_EPS:
            suspect.append(SuspectWick(t, "low", b.low, body_lo))
            new_low = body_lo
        if b.high > hi + tol + _WICK_EPS and body_hi <= hi + tol / 2 + _WICK_EPS:
            suspect.append(SuspectWick(t, "high", b.high, body_hi))
            new_high = body_hi
        out.append(b if (new_low, new_high) == (b.low, b.high) else b.model_copy(update={"low": new_low, "high": new_high}))
    return WickCheck(suspect, tol, out)


@dataclass(frozen=True)
class BackToEntry:
    window_min: int
    bars: int  # bars found in the window
    complete: bool  # bars cover the whole window
    returned_at: datetime | None  # first bar after the exit that touched the entry price


def back_to_entry(
    bars: list[Candle],
    *,
    avg_in: float,
    exit_px: float,
    exit_ts: datetime,
    window_min: int = 30,
) -> BackToEntry:
    """Did the premium get back to the average entry price within ``window_min`` of the exit?

    Exited under cost: first bar whose high reaches ``avg_in``. Exited at or over cost: first
    bar whose low falls to ``avg_in``. Direction only, no dollars (no "left on the table").
    """
    e = _utc(exit_ts)
    end = e + timedelta(minutes=window_min)
    inside = sorted((b for b in bars if e <= _utc(b.ts) < end), key=lambda b: _utc(b.ts))
    if not inside:
        return BackToEntry(window_min, 0, False, None)
    tf = timedelta(minutes=_tf_minutes(bars))
    if exit_px < avg_in:
        hit = next((b for b in inside if b.high >= avg_in), None)
    else:
        hit = next((b for b in inside if b.low <= avg_in), None)
    return BackToEntry(
        window_min, len(inside), _utc(inside[-1].ts) + tf >= end, _utc(hit.ts) if hit else None
    )


def compute_excursions(
    bars: list[Candle],
    *,
    avg_in: float,
    qty: int,
    risk: float | None,
    open_ts: datetime,
    close_ts: datetime | None,
    mult: int = CONTRACT_MULT,
    windows: tuple[int, ...] = POST_EXIT_WINDOWS_MIN,
    fills: list[Fill] | None = None,
    trim_wicks: bool = True,
) -> Excursions:
    """Everything for one position. ``close_ts`` None (still open): the hold runs
    to the last bar and there is no post-exit path.

    With ``fills`` the hold is bounded at its edge minutes (see ``hold_excursion``) and,
    when ``trim_wicks``, suspect wicks on the interior bars are trimmed first; the result
    carries the ``WickCheck`` and, if wicks were trimmed, ``raw_hold`` (same bounds, wicks
    as printed). This is the one definition the trade review and the rollup share."""
    end = close_ts
    if end is None and bars:
        tf = timedelta(minutes=_tf_minutes(bars))
        end = max(_utc(b.ts) for b in bars) + tf
    hold_bars, check, raw_hold = bars, None, None
    if fills and end is not None:
        check = check_hold_wicks(
            bars, fill_prices=[p for _, p in fills], avg_in=avg_in, open_ts=open_ts, close_ts=end
        )
        if trim_wicks:
            hold_bars = check.bars
    kw = dict(avg_in=avg_in, qty=qty, risk=risk, open_ts=open_ts, close_ts=end, mult=mult,
              fills=fills, closed=close_ts is not None)
    hold = hold_excursion(hold_bars, **kw) if end is not None else None
    if check is not None and check.suspect and trim_wicks:
        raw_hold = hold_excursion(bars, **kw)
    post = (
        post_exit_path(
            bars, avg_in=avg_in, qty=qty, risk=risk, exit_ts=close_ts, windows=windows, mult=mult
        )
        if close_ts is not None
        else []
    )
    return Excursions(
        avg_in=avg_in, qty=qty, risk=risk, stop_price=stop_price(avg_in, qty, risk, mult),
        hold=hold, post_exit=post, wicks=check, raw_hold=raw_hold,
    )


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


# ----------------------------------------------------------------------------
# Cross-trade rollup (#52): held past stop / exited early / time in drawdown.
# Pure: callers hand in one ``ExcursionFact`` per measured trade.

EXITED_EARLY_WINDOW_MIN = 30
EXITED_EARLY_THRESHOLD_R = 1.0
_EPS = 1e-9

HELD_PAST_STOP = "held_past_stop"
EXITED_EARLY = "exited_early"
EXCURSION_PATTERN_CODES: tuple[str, ...] = (HELD_PAST_STOP, EXITED_EARLY)


def beyond_exit_r(
    bars: list[Candle],
    *,
    exit_px: float,
    qty: int,
    risk: float | None,
    exit_ts: datetime,
    window_min: int = EXITED_EARLY_WINDOW_MIN,
    mult: int = CONTRACT_MULT,
) -> float | None:
    """How far price ran beyond the exit, in R measured from the exit price.

    ``(max bar high in [exit_ts, exit_ts + window_min) - exit_px) * mult * qty / risk``,
    floored at 0 (never negative). ``risk`` is the ledger's dollars-at-risk (same R
    as everywhere else), ``qty`` the contracts bought. None when there are no bars
    in the window or no risk. Unlike ``PostExitWindow.best_r`` (measured on average
    cost) this starts at the exit price, so it answers "how much more was there
    after I sold", not "how good was the trade".
    """
    if not risk or qty <= 0:
        return None
    e = _utc(exit_ts)
    end = e + timedelta(minutes=window_min)
    inside = [b for b in bars if e <= _utc(b.ts) < end]
    if not inside:
        return None
    best = max(b.high for b in inside)
    return max(0.0, (best - exit_px) * mult * qty / risk)


@dataclass(frozen=True)
class ExcursionFact:
    """What the rollup needs from one *closed* trade that has bars covering its hold."""

    trade_id: int
    pnl: float  # net P&L (ledger, fees in)
    risk: float | None  # ledger dollars-at-risk (the R unit)
    crossed_stop: bool
    drawdown_minutes: int
    beyond_exit_r: float | None  # None when no bars follow the exit


def held_past_stop(f: ExcursionFact) -> bool:
    """Price crossed the stop line (a bar low reached it) during the hold."""
    return f.crossed_stop


def recovered(f: ExcursionFact) -> bool:
    """Closed at or above break-even (pnl >= 0)."""
    return f.pnl >= 0


def exited_early(f: ExcursionFact) -> bool:
    """A winner (pnl > 0) whose best high within 30 minutes after the exit was at
    least 1R beyond the exit price (see ``beyond_exit_r``)."""
    return (
        f.pnl > 0
        and f.beyond_exit_r is not None
        and f.beyond_exit_r >= EXITED_EARLY_THRESHOLD_R - _EPS
    )


def excursion_codes(f: ExcursionFact | None) -> list[str]:
    if f is None:
        return []
    out = []
    if held_past_stop(f):
        out.append(HELD_PAST_STOP)
    if exited_early(f):
        out.append(EXITED_EARLY)
    return out


def excursion_tag_spec(f: ExcursionFact, code: str) -> tuple[str, str]:
    """(kind, text) for a position tag. kind: r = red flag, w = warning, a = note."""
    if code == HELD_PAST_STOP:
        if recovered(f):
            return "w", "held past stop, recovered"
        return "r", "held past stop, did not recover"
    return "a", f"exited early (ran {f.beyond_exit_r:.1f}R more in {EXITED_EARLY_WINDOW_MIN} min)"


@dataclass(frozen=True)
class ExcursionRollup:
    n_trades: int  # closed trades considered
    n_measured: int  # of those, with bars covering the hold
    n_skipped: int  # n_trades - n_measured (no bars); excluded from every count below
    held_past_stop: int
    recovered: int  # held past stop and closed with pnl >= 0
    not_recovered: int  # held past stop and closed with pnl < 0
    not_recovered_cost_usd: float
    not_recovered_cost_r: float | None
    exited_early: int
    winners_checked: int  # measured winners with post-exit bars (the exited-early denominator)
    median_drawdown_minutes: float | None


def rollup_excursions(facts: list[ExcursionFact], n_trades: int | None = None) -> ExcursionRollup:
    """Roll measured trades up into the three headline numbers.

    * **Held past stop**: ``crossed_stop`` trades. ``recovered`` = pnl >= 0;
      ``not_recovered`` = pnl < 0. Their **cost** is the net P&L beyond the stop
      line: ``pnl + risk`` per trade (negative = lost more than the stop allowed,
      positive = lost less than the stop, e.g. bounced before the exit), summed in
      dollars; in R it is the sum of ``(pnl + risk) / risk``.
    * **Exited early**: see ``exited_early`` (winner, best high in the next 30 min
      >= 1R beyond the exit price, R on the ledger risk).
    * **Median time in drawdown**: median ``drawdown_minutes`` (bars closing below
      average cost during the hold) across measured trades.

    ``n_trades`` is how many closed trades were considered (default: len(facts));
    trades without bars are not in ``facts`` and show up as ``n_skipped``.
    """
    n_measured = len(facts)
    total = n_measured if n_trades is None else n_trades
    hps = [f for f in facts if held_past_stop(f)]
    lost = [f for f in hps if not recovered(f)]
    cost_usd = round(sum(f.pnl + (f.risk or 0.0) for f in lost), 2)
    r_parts = [(f.pnl + f.risk) / f.risk for f in lost if f.risk]
    winners = [f for f in facts if f.pnl > 0 and f.beyond_exit_r is not None]
    dd = [f.drawdown_minutes for f in facts]
    return ExcursionRollup(
        n_trades=total, n_measured=n_measured, n_skipped=max(0, total - n_measured),
        held_past_stop=len(hps), recovered=len(hps) - len(lost), not_recovered=len(lost),
        not_recovered_cost_usd=cost_usd,
        not_recovered_cost_r=round(sum(r_parts), 3) if r_parts else (0.0 if not lost else None),
        exited_early=sum(1 for f in facts if exited_early(f)),
        winners_checked=len(winners),
        median_drawdown_minutes=statistics.median(dd) if dd else None,
    )
