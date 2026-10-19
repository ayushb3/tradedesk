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

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.market import Candle

CONTRACT_MULT = 100
POST_EXIT_WINDOWS_MIN: tuple[int, ...] = (15, 30, 60)


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


def stop_price(avg_in: float, qty: int, risk: float | None, mult: int = CONTRACT_MULT) -> float | None:
    """Premium level at which the position has lost ``risk`` dollars, or None."""
    if not risk or qty <= 0 or risk <= 0:
        return None
    level = avg_in - risk / (mult * qty)
    return round(level, 4) if level > 0 else None


def hold_excursion(
    bars: list[Candle],
    *,
    avg_in: float,
    qty: int,
    risk: float | None,
    open_ts: datetime,
    close_ts: datetime,
    mult: int = CONTRACT_MULT,
) -> HoldExcursion | None:
    """MAE/MFE, time in drawdown and stop crossing over ``[open_ts, close_ts]``.

    Returns None when no bar overlaps the hold.
    """
    if qty <= 0 or not bars:
        return None
    o, c = _utc(open_ts), _utc(close_ts)
    tf = timedelta(minutes=_tf_minutes(bars))
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
) -> Excursions:
    """Everything for one position. ``close_ts`` None (still open): the hold runs
    to the last bar and there is no post-exit path."""
    end = close_ts
    if end is None and bars:
        tf = timedelta(minutes=_tf_minutes(bars))
        end = max(_utc(b.ts) for b in bars) + tf
    hold = (
        hold_excursion(
            bars, avg_in=avg_in, qty=qty, risk=risk, open_ts=open_ts, close_ts=end, mult=mult
        )
        if end is not None
        else None
    )
    post = (
        post_exit_path(
            bars, avg_in=avg_in, qty=qty, risk=risk, exit_ts=close_ts, windows=windows, mult=mult
        )
        if close_ts is not None
        else []
    )
    return Excursions(
        avg_in=avg_in, qty=qty, risk=risk, stop_price=stop_price(avg_in, qty, risk, mult),
        hold=hold, post_exit=post,
    )


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)
