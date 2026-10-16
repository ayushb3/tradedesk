"""Deterministic synthetic OHLCV candle generation.

Pure-Python + numpy. No network, no vendor data. This is the reference
generator that every other mock module (fills, quant levels, replay) trusts:
given the same ``(symbol, day)`` it always returns byte-for-byte identical
bars, so fills land inside candle ranges and computed levels stay stable.

Design:
    * 1-minute bars covering **extended hours**: premarket 04:00 ET through
      after-hours 20:00 ET (960 bars). Later milestones use the full window to
      compute premarket high/low and prior-day levels.
    * ``ts`` is the bar OPEN time in **UTC**, DST-correctly converted from the
      configured ET timezone via :mod:`zoneinfo`.
    * Realistic intraday shape: calm premarket, an opening-drive volatility and
      volume burst at 09:30 ET, a busy morning, low-vol midday chop, a rising
      afternoon into a higher-volume close, then quiet after-hours.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.models.market import Candle

UTC = ZoneInfo("UTC")

# Extended-hours window expressed as ET wall-clock minutes since midnight.
PREMARKET_OPEN_MIN = 4 * 60        # 04:00 ET
REG_OPEN_MIN = 9 * 60 + 30         # 09:30 ET
REG_CLOSE_MIN = 16 * 60            # 16:00 ET
AFTERHOURS_CLOSE_MIN = 20 * 60     # 20:00 ET
N_BARS = AFTERHOURS_CLOSE_MIN - PREMARKET_OPEN_MIN  # 960 one-minute bars

# Anchor prices for well-known tickers; everything else is hash-derived.
#
# Index symbols (SPX/SPXW, NDX, RUT, VIX, XSP) trade at index scale — orders of
# magnitude above a normal equity. Their option strikes are index-scale too
# (e.g. an SPXW put struck at 7575). Without an anchor here the hash-derived
# ``start_price`` puts them in the ~$8–$300 equity band, so a Black-Scholes
# premium struck at 7575 against a ~$260 "underlying" comes out as deep-ITM
# intrinsic (~7315) instead of a plausible index-option premium. Anchoring the
# index roots at index scale keeps strike and underlying on the same scale.
KNOWN_START: dict[str, float] = {
    "RIVN": 15.0,
    "HOOD": 25.0,
    "AAPL": 190.0,
    "TSLA": 250.0,
    "NVDA": 120.0,
    "AMD": 160.0,
    "SOFI": 8.0,
    "PLTR": 28.0,
    "SPY": 540.0,
    # Index roots (index scale, not equity scale).
    "SPX": 7575.0,
    "SPXW": 7575.0,
    "NDX": 20000.0,
    "RUT": 2200.0,
    "XSP": 757.0,
}

# Index roots move far less on a percentage basis than a single equity — a broad
# index rarely swings more than ~1% intraday, whereas the default equity path
# can wander ~5%. A 5% wander is fine for a $15 stock but catastrophic for a
# near-ATM index option: it whipsaws a 7575-strike put from ~$0 to >$100 within
# a session, so the premium series (and its MFE/MAE) can't stay on the scale of
# a real sub-$1 fill. Damp the per-minute return volatility for index roots so a
# near-the-money index option keeps a plausible, fill-scale premium.
INDEX_ROOTS: frozenset[str] = frozenset({"SPX", "SPXW", "NDX", "RUT", "XSP"})
_INDEX_VOL_SCALE = 0.02  # index per-minute sigma ≈ 2% of the equity default


def seed_for(symbol: str, day: date) -> int:
    """Return a stable 32-bit seed for ``(symbol, day)``.

    Uses SHA-256 rather than Python's salted ``hash()`` so the seed (and thus
    every generated bar) is reproducible across processes and interpreter runs.
    """
    digest = hashlib.sha256(f"{symbol}{day.isoformat()}".encode()).hexdigest()
    return int(digest, 16) % 2**32


def start_price(symbol: str, seed: int) -> float:
    """Deterministic opening reference price for ``symbol``.

    Known tickers use their anchor; unknown tickers get a stable value in a
    sane ~$8–$300 range derived from the seed.
    """
    if symbol.upper() in KNOWN_START:
        return KNOWN_START[symbol.upper()]
    return round(8.0 + (seed % 29200) / 100.0, 2)


def _regime_profiles(etmin: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map each bar's ET minute-of-day to a (vol_mult, vol_base) profile.

    ``vol_mult`` scales per-minute return volatility; ``vol_base`` is the
    baseline share volume before per-bar noise.
    """
    vol_mult = np.full(etmin.shape, 1.0, dtype=float)
    vol_base = np.full(etmin.shape, 1000.0, dtype=float)

    premarket = etmin < REG_OPEN_MIN
    drive = (etmin >= REG_OPEN_MIN) & (etmin < REG_OPEN_MIN + 30)
    morning = (etmin >= REG_OPEN_MIN + 30) & (etmin < 12 * 60)
    midday = (etmin >= 12 * 60) & (etmin < 14 * 60)
    afternoon = (etmin >= 14 * 60) & (etmin < REG_CLOSE_MIN)
    afterhours = etmin >= REG_CLOSE_MIN

    vol_mult[premarket], vol_base[premarket] = 0.5, 500.0
    vol_mult[drive], vol_base[drive] = 2.6, 9000.0
    vol_mult[morning], vol_base[morning] = 1.2, 4000.0
    vol_mult[midday], vol_base[midday] = 0.6, 1800.0
    vol_mult[afternoon], vol_base[afternoon] = 1.3, 4500.0
    vol_mult[afterhours], vol_base[afterhours] = 0.5, 700.0

    # Power-hour ramp: volume climbs steadily into the 16:00 close.
    if afternoon.any():
        span = np.clip((etmin[afternoon] - 14 * 60) / (REG_CLOSE_MIN - 14 * 60), 0, 1)
        vol_base[afternoon] *= 1.0 + span  # up to ~2x by the bell

    return vol_mult, vol_base


def generate_candles(
    symbol: str,
    day: date,
    timeframe: str = "1m",
    tz: str = "America/New_York",
) -> list[Candle]:
    """Generate one trading day of deterministic 1-minute OHLCV bars.

    Args:
        symbol: Ticker; drives the anchor price and (with ``day``) the seed.
        day: ET calendar/session date.
        timeframe: Stamped onto each returned :class:`Candle` (bars are always
            1-minute internally; this label is passed through unchanged).
        tz: ET timezone name used for the DST-correct ET->UTC conversion.

    Returns:
        ``N_BARS`` (~960) candles, strictly increasing by ``ts`` (UTC bar-open),
        no gaps, with ``high >= max(open, close)``, ``low <= min(open, close)``,
        ``low > 0`` and ``volume > 0``.
    """
    seed = seed_for(symbol, day)
    rng = np.random.default_rng(seed)
    start = start_price(symbol, seed)

    idx = np.arange(N_BARS)
    etmin = PREMARKET_OPEN_MIN + idx
    vol_mult, vol_base = _regime_profiles(etmin)

    # --- price path: gentle random walk with regime-scaled volatility ---
    # Index roots move far less (percentage-wise) than a single equity.
    base_sigma = 0.0008
    drift_sigma = 3e-5
    if symbol.upper() in INDEX_ROOTS:
        base_sigma *= _INDEX_VOL_SCALE
        drift_sigma *= _INDEX_VOL_SCALE
    sigma = base_sigma * vol_mult
    rets = rng.normal(0.0, 1.0, N_BARS) * sigma
    rets += rng.normal(0.0, drift_sigma, N_BARS)  # faint overall drift

    # Opening-drive: a directional burst right at 09:30 ET.
    drive_mask = (etmin >= REG_OPEN_MIN) & (etmin < REG_OPEN_MIN + 30)
    drive_dir = 1.0 if rng.random() > 0.5 else -1.0
    rets[drive_mask] += drive_dir * base_sigma * 1.5

    closes = start * np.cumprod(1.0 + rets)
    opens = np.empty(N_BARS)
    opens[0] = start
    opens[1:] = closes[:-1]

    # --- intrabar wicks: high/low extend beyond the body ---
    body_hi = np.maximum(opens, closes)
    body_lo = np.minimum(opens, closes)
    up_wick = np.abs(rng.normal(0.0, 1.0, N_BARS)) * sigma * closes
    dn_wick = np.abs(rng.normal(0.0, 1.0, N_BARS)) * sigma * closes
    highs = body_hi + up_wick
    lows = np.maximum(body_lo - dn_wick, 0.01)

    # --- volume: regime baseline plus positive noise ---
    volumes = vol_base * (1.0 + np.abs(rng.normal(0.0, 0.4, N_BARS)))

    # --- timestamps: bar-open ET wall clock -> UTC (DST-correct) ---
    et = ZoneInfo(tz)
    base_et = datetime.combine(day, time(PREMARKET_OPEN_MIN // 60, PREMARKET_OPEN_MIN % 60), tzinfo=et)

    out: list[Candle] = []
    for i in range(N_BARS):
        ts = (base_et + timedelta(minutes=i)).astimezone(UTC)
        out.append(
            Candle(
                symbol=symbol,
                ts=ts,
                open=round(float(opens[i]), 2),
                high=round(float(highs[i]), 2),
                low=round(float(lows[i]), 2),
                close=round(float(closes[i]), 2),
                volume=float(round(float(volumes[i]))),
                timeframe=timeframe,
            )
        )
    return out


# Minutes-per-bar for each supported timeframe. "1d" collapses the whole session
# into a single bar. Keep in sync with the replay route's _SUPPORTED_TIMEFRAMES.
_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": None}


def resample_candles(
    candles: list[Candle],
    timeframe: str,
    tz: str = "America/New_York",
) -> list[Candle]:
    """Aggregate 1-minute ``candles`` into ``timeframe`` OHLCV bars.

    Buckets are aligned to the ET wall clock (5m bars open at :00/:05/…, 15m at
    :00/:15/:30/:45, 1h at the top of the hour), matching how a real feed buckets.
    ``1d`` collapses the entire session into one bar. ``1m`` (or an unknown token)
    returns the input unchanged. Each output bar: open of the first sub-bar, close
    of the last, max high, min low, summed volume; ``ts`` is the bucket-open in UTC.

    Assumes ``candles`` are 1-minute, chronological, and from a single session.
    """
    minutes = _TIMEFRAME_MINUTES.get(timeframe, 1)
    if minutes == 1 or not candles:
        return [c.model_copy(update={"timeframe": timeframe}) for c in candles]

    et = ZoneInfo(tz)

    def bucket_key(c: Candle) -> int:
        et_dt = c.ts.astimezone(et)
        etmin = et_dt.hour * 60 + et_dt.minute
        if minutes is None:  # 1d — one bucket for the whole session
            return 0
        return etmin // minutes

    out: list[Candle] = []
    group: list[Candle] = []
    current_key: int | None = None

    def flush(bars: list[Candle]) -> None:
        if not bars:
            return
        out.append(
            Candle(
                symbol=bars[0].symbol,
                ts=bars[0].ts,  # bucket-open time (UTC)
                open=bars[0].open,
                high=max(b.high for b in bars),
                low=min(b.low for b in bars),
                close=bars[-1].close,
                volume=float(sum(b.volume for b in bars)),
                timeframe=timeframe,
            )
        )

    for c in candles:
        k = bucket_key(c)
        if current_key is None or k == current_key:
            group.append(c)
            current_key = k
        else:
            flush(group)
            group = [c]
            current_key = k
    flush(group)
    return out
