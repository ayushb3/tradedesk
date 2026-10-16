"""Option premium math + synthetic premium-series builders (M7b).

Pure, dependency-light functions (numpy + stdlib) so they unit-test without a
DB or HTTP. Two producers of a premium OHLCV series from an *underlying* candle
series:

* :func:`synthesize_mock_premium_bars` — the MockProvider's own
  ``get_option_bars``. A deterministic premium curve seeded per contract key
  (Black-Scholes with a contract-seeded IV + light per-bar noise). Reproducible
  given the same contract + underlying candles. Tagged ``method="provider"``.

* :func:`bs_interp_premium_bars` — the provider-agnostic **fallback** used when
  a provider returns ``None`` (webull has no historical option bars). Black-
  Scholes with IV *solved so the curve passes through the actual recorded fill
  prices* (anchor points), IV linearly interpolated between anchors. Tagged
  ``method="bs_interp"``, ``synthetic=True``.

No dividends, ``r=0`` — this is a review aid (where did premium peak / how fast
did it decay), not a pricing engine. Time-to-expiry is measured from each bar to
16:00 America/New_York on the expiry date, so intraday theta decay shows.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.models.market import Candle, OptionContract

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

SECONDS_PER_YEAR = 365.25 * 24 * 3600
# Floor on time-to-expiry (~1 minute in years) so BS never divides by zero at
# the expiry instant; premium collapses to intrinsic there.
_T_FLOOR = 60.0 / SECONDS_PER_YEAR
_IV_LO, _IV_HI = 0.01, 5.0


# --------------------------------------------------------------------------- #
# Black-Scholes core
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, sigma: float, option_type: str) -> float:
    """Black-Scholes price (r=0, no dividends). ``option_type`` is CALL/PUT.

    Degenerate inputs collapse to intrinsic value, which keeps the curve sane
    at/near expiry and for zero vol.
    """
    is_call = str(option_type).upper() == "CALL"
    intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
    T = max(T, _T_FLOOR)
    if sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        return intrinsic
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if is_call:
        return S * _norm_cdf(d1) - K * _norm_cdf(d2)
    return K * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_vol(
    target: float, S: float, K: float, T: float, option_type: str
) -> float:
    """Solve BS implied vol so ``bs_price == target`` via bisection.

    Clamped to ``[_IV_LO, _IV_HI]``. Targets below intrinsic (can happen with
    noisy anchors) clamp to the low bound; targets above the achievable max
    clamp to the high bound. Robust — no derivative, always terminates.
    """
    T = max(T, _T_FLOOR)
    lo, hi = _IV_LO, _IV_HI
    p_lo = bs_price(S, K, T, lo, option_type)
    p_hi = bs_price(S, K, T, hi, option_type)
    if target <= p_lo:
        return lo
    if target >= p_hi:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        p_mid = bs_price(S, K, T, mid, option_type)
        if abs(p_mid - target) < 1e-6:
            return mid
        if p_mid < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def time_to_expiry_years(ts: datetime, expiry: str) -> float:
    """Years from ``ts`` (UTC) to 16:00 ET on the ``expiry`` date, floored."""
    exp_date = date.fromisoformat(expiry[:10])
    exp_moment = datetime.combine(exp_date, time(16, 0), tzinfo=ET).astimezone(UTC)
    ts_u = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    ts_u = ts_u.astimezone(UTC)
    secs = (exp_moment - ts_u).total_seconds()
    return max(secs, 60.0) / SECONDS_PER_YEAR


# --------------------------------------------------------------------------- #
# Premium OHLC from an underlying candle
# --------------------------------------------------------------------------- #
def _premium_ohlc(
    candle: Candle, K: float, T: float, sigma: float, option_type: str
) -> tuple[float, float, float, float]:
    """Map one underlying OHLC to a premium OHLC via BS at fixed (sigma, T).

    Calls rise with the underlying (o->o, h->h, ...); puts invert (the
    underlying high produces the premium low). h/l are re-derived as the
    max/min so the bar is always internally consistent.
    """
    is_call = str(option_type).upper() == "CALL"
    p_open = bs_price(candle.open, K, T, sigma, option_type)
    p_close = bs_price(candle.close, K, T, sigma, option_type)
    if is_call:
        p_hi = bs_price(candle.high, K, T, sigma, option_type)
        p_lo = bs_price(candle.low, K, T, sigma, option_type)
    else:
        p_hi = bs_price(candle.low, K, T, sigma, option_type)
        p_lo = bs_price(candle.high, K, T, sigma, option_type)
    hi = max(p_open, p_close, p_hi, p_lo)
    lo = min(p_open, p_close, p_hi, p_lo)
    return p_open, hi, max(lo, 0.0), p_close


def _seed_iv(contract_key: str) -> float:
    """Deterministic per-contract base IV in [0.30, 0.80)."""
    h = int(hashlib.sha256(contract_key.encode()).hexdigest(), 16)
    return 0.30 + (h % 500) / 1000.0


# --------------------------------------------------------------------------- #
# Producers
# --------------------------------------------------------------------------- #
def synthesize_mock_premium_bars(
    underlying: list[Candle],
    contract: OptionContract,
    timeframe: str = "1m",
) -> list[Candle]:
    """Deterministic mock premium series from underlying candles.

    Fixed contract-seeded IV plus a tiny deterministic per-bar wiggle so the
    curve looks like a traded contract rather than a smooth BS line. No fills
    needed — this is the MockProvider's ``get_option_bars`` output.
    """
    key = contract.contract_key
    base_iv = _seed_iv(key)
    out: list[Candle] = []
    for c in underlying:
        # Deterministic +/-5% IV wiggle keyed on (contract, bar timestamp).
        wig = int(hashlib.sha256(f"{key}{c.ts.isoformat()}".encode()).hexdigest(), 16)
        sigma = base_iv * (1.0 + ((wig % 100) - 50) / 1000.0)
        T = time_to_expiry_years(c.ts, contract.expiry)
        o, h, l, cl = _premium_ohlc(c, contract.strike, T, sigma, contract.option_type)
        out.append(
            Candle(
                symbol=key,
                ts=c.ts,
                open=round(o, 4),
                high=round(h, 4),
                low=round(l, 4),
                close=round(cl, 4),
                volume=round(c.volume * 0.15, 2),
                timeframe=timeframe,
            )
        )
    return out


def _interp_iv(anchors: list[tuple[datetime, float]], ts: datetime) -> float:
    """Piecewise-linear IV at ``ts`` from time-sorted (ts, iv) anchors.

    Flat-extrapolates before the first / after the last anchor.
    """
    if not anchors:
        return 0.5
    if ts <= anchors[0][0]:
        return anchors[0][1]
    if ts >= anchors[-1][0]:
        return anchors[-1][1]
    for (t0, v0), (t1, v1) in zip(anchors, anchors[1:]):
        if t0 <= ts <= t1:
            span = (t1 - t0).total_seconds()
            if span <= 0:
                return v1
            w = (ts - t0).total_seconds() / span
            return v0 + w * (v1 - v0)
    return anchors[-1][1]


def bs_interp_premium_bars(
    underlying: list[Candle],
    contract: OptionContract,
    fills: list[tuple[datetime, float]],
    timeframe: str = "1m",
) -> list[Candle]:
    """Synthetic fallback: BS premium with IV solved to pass through fills.

    ``fills`` are ``(ts, premium_price)`` anchor points (the trade's actual
    executions). For each anchor we solve the IV that reprices BS to the fill
    at that bar's underlying close, then interpolate IV linearly between
    anchors. Each anchor bar's close is snapped exactly to the fill price so the
    curve provably passes through every recorded fill regardless of solver
    convergence.
    """
    if not underlying:
        return []

    def _u(ts: datetime) -> datetime:
        return (ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)).astimezone(UTC)

    # Index underlying by UTC timestamp for anchor lookup.
    by_ts = {_u(c.ts): c for c in underlying}
    ordered_ts = sorted(by_ts)

    def _nearest(ts: datetime) -> Candle:
        tsu = _u(ts)
        if tsu in by_ts:
            return by_ts[tsu]
        return min(underlying, key=lambda c: abs((_u(c.ts) - tsu).total_seconds()))

    # Solve IV at each anchor.
    anchors: list[tuple[datetime, float]] = []
    anchor_price: dict[datetime, float] = {}
    for ts, price in sorted(fills, key=lambda f: _u(f[0])):
        tsu = _u(ts)
        c = _nearest(ts)
        T = time_to_expiry_years(c.ts, contract.expiry)
        iv = implied_vol(price, c.close, contract.strike, T, contract.option_type)
        anchors.append((tsu, iv))
        anchor_price[_u(c.ts)] = price

    out: list[Candle] = []
    for c in underlying:
        tsu = _u(c.ts)
        sigma = _interp_iv(anchors, tsu)
        T = time_to_expiry_years(c.ts, contract.expiry)
        o, h, l, cl = _premium_ohlc(c, contract.strike, T, sigma, contract.option_type)
        # Snap anchor bars to the exact recorded fill price.
        if tsu in anchor_price:
            cl = anchor_price[tsu]
            h = max(h, cl)
            l = min(l, cl) if l > 0 else max(cl * 0.98, 0.0)
        out.append(
            Candle(
                symbol=contract.contract_key,
                ts=c.ts,
                open=round(o, 4),
                high=round(max(h, 0.0), 4),
                low=round(max(l, 0.0), 4),
                close=round(max(cl, 0.0), 4),
                volume=round(c.volume * 0.15, 2),
                timeframe=timeframe,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Excursion (MFE / MAE) — PRD R9
# --------------------------------------------------------------------------- #
def compute_excursion(
    bars: list[Candle],
    entry_price: float,
    is_long: bool,
    open_ts: datetime,
    close_ts: datetime | None,
) -> tuple[float, float, float, float] | None:
    """Max favorable / adverse excursion over the hold window.

    Returns ``(mfe, mae, mfe_pct, mae_pct)`` in price points and percent of
    entry, both magnitudes clamped to >= 0, or ``None`` if no bars fall in the
    window. Favorable/adverse are relative to the position direction: a long
    profits when price rises; a short (sold option / short stock) when it falls.
    """
    def _u(ts: datetime) -> datetime:
        return (ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)).astimezone(UTC)

    if not bars:
        return None

    o = _u(open_ts)
    c = _u(close_ts) if close_ts is not None else None
    ordered = sorted(bars, key=lambda b: _u(b.ts))

    # A bar stamped at its OPEN time covers the interval [ts, next_ts). Select
    # bars whose interval overlaps the hold window, not just those whose open
    # stamp falls strictly inside it — otherwise a sub-bar hold (e.g. a 20s
    # option scalp between two 1-minute bar opens) would match no bar and the
    # excursion would be null. The overlap test: a bar starting at or before the
    # window close, and ending (next bar's open) at or after the window open.
    window: list[Candle] = []
    for i, b in enumerate(ordered):
        start = _u(b.ts)
        end = _u(ordered[i + 1].ts) if i + 1 < len(ordered) else None
        starts_by_close = c is None or start <= c
        ends_after_open = end is None or end > o
        if starts_by_close and ends_after_open:
            window.append(b)
    if not window:
        # Hold window sits entirely outside the series (or degenerate): fall
        # back to the single bar whose interval is nearest the entry instant so
        # excursion is still defined for an in-session trade.
        window = [min(ordered, key=lambda b: abs((_u(b.ts) - o).total_seconds()))]
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    if is_long:
        mfe = max(hi - entry_price, 0.0)
        mae = max(entry_price - lo, 0.0)
    else:
        mfe = max(entry_price - lo, 0.0)
        mae = max(hi - entry_price, 0.0)
    denom = entry_price if entry_price else 1.0
    return (
        round(mfe, 4),
        round(mae, 4),
        round(100.0 * mfe / denom, 4),
        round(100.0 * mae / denom, 4),
    )
