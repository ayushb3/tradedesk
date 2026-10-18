"""Replay-session assembler — stitches candles + causal indicators + levels.

``compute_replay_session`` fetches a session's candles (plus the prior trading
day for PDH/PDL) and computes every indicator/level array aligned index-for-index
with those candles. Nothing here looks ahead: each array is built by the pure,
rolling functions in this package, so truncating ``candles`` to ``[:i+1]`` yields
identical leading values.

``compute_scenario_session`` extends this for M6 training drills: it computes all
indicators/levels on the FULL session first, then slices to ``[start_bar, end_bar)``
(left-inclusive, right-exclusive — Python slice semantics). This ordering is the
key causal-alignment guarantee: slicing never changes the values, it only limits
what the user sees. See the function docstring for the full contract.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.models.market import Candle
from app.models.replay import ReplaySessionResponse
from app.models.training import DrillType, ScenarioSessionResponse, ScenarioSource
from app.providers.base import DataProvider

from . import levels as lv
from .ema import ema
from .rsi import rsi
from .vwap import vwap

# Defensive import: task 6.0.2 (training_store) may not be landed yet.
# We only need the type for annotation; structural duck-typing is fine at runtime.
try:
    from app.db.training_store import ScenarioFull  # type: ignore[import-not-found]
except ImportError:  # 6.0.2 not yet landed; still allow signature-only use in tests
    ScenarioFull = Any  # type: ignore[assignment,misc]  # noqa: N818

_ROUND = 4

# Default indicator configuration. The replay route may override these via query
# params; the scenario/drill path always uses these defaults so its causal
# alignment and stored expectations are unaffected.
DEFAULT_EMA_PERIODS: tuple[int, ...] = (9, 21, 50, 200)
DEFAULT_RSI_PERIOD = 14

# Fixed anchor for blind-mode timestamp rebasing.
# All sliced candles have their timestamps rebased so the first bar of the slice
# opens at this UTC moment; subsequent bars preserve intra-session spacing (delta
# from the preceding bar).
# The choice of 2001-01-01 13:30:00 UTC is arbitrary but stable — it corresponds
# to 09:30 ET (regular-session open) on a Monday, which keeps indicator logic
# that keys on ET wall-clock time self-consistent for regular-session slices.
_BLIND_ANCHOR_UTC = datetime(2001, 1, 1, 13, 30, 0, tzinfo=timezone.utc)


def _prior_trading_day(d: date) -> date:
    """Walk back one calendar day, skipping weekends, to the prior session."""
    p = d - timedelta(days=1)
    while p.weekday() >= 5:  # 5=Sat, 6=Sun
        p -= timedelta(days=1)
    return p


def _round_series(series: list[float | None]) -> list[float | None]:
    return [None if v is None else round(float(v), _ROUND) for v in series]


def _build_indicators(
    candles: list[Candle],
    tz: str,
    *,
    ema_periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
) -> dict[str, list[float | None]]:
    """Compute the indicator series map for a candle list.

    EMA series are keyed ``ema<period>`` (e.g. ``ema9``) for each requested period;
    duplicates are collapsed and order preserved. ``rsi14``-style key follows the
    requested period (``rsi<period>``). VWAP is always included (cheap, session-anchored).
    """
    closes = [c.close for c in candles]

    # Preserve order, drop duplicate periods.
    seen: set[int] = set()
    periods = [p for p in ema_periods if not (p in seen or seen.add(p))]

    indicators: dict[str, list[float | None]] = {}
    for p in periods:
        indicators[f"ema{p}"] = _round_series(ema(closes, p))
    indicators["vwap"] = _round_series(vwap(candles, tz=tz))
    indicators[f"rsi{rsi_period}"] = _round_series(rsi(closes, rsi_period))
    return indicators


def _build_levels(
    candles: list[Candle],
    prior_candles: list[Candle],
    tz: str,
    or_minutes: int,
) -> dict[str, list[float | None]]:
    level_series = {
        "pdh": lv.pdh(candles, prior_candles, tz),
        "pdl": lv.pdl(candles, prior_candles, tz),
        "pmh": lv.pmh(candles, tz),
        "pml": lv.pml(candles, tz),
        "day_high": lv.day_high(candles, tz),
        "day_low": lv.day_low(candles, tz),
        "or_high": lv.or_high(candles, or_minutes, tz),
        "or_low": lv.or_low(candles, or_minutes, tz),
    }
    return {k: _round_series(v) for k, v in level_series.items()}


def _fetch_full_session(
    provider: DataProvider,
    symbol: str,
    session_date: date,
    tz: str,
    or_minutes: int,
    *,
    timeframe: str = "1m",
    ema_periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
) -> tuple[list[Candle], dict[str, list[float | None]], dict[str, list[float | None]]]:
    """Fetch candles and compute all indicators + levels for the full session.

    Returns ``(candles, indicators, levels)`` — all arrays are len(candles) and
    aligned index-for-index. No slicing is done here; callers slice afterward.
    """
    candles: list[Candle] = provider.get_candles(symbol, session_date, timeframe)
    prior_candles: list[Candle] = provider.get_candles(
        symbol, _prior_trading_day(session_date), timeframe
    )

    indicators = _build_indicators(
        candles, tz, ema_periods=ema_periods, rsi_period=rsi_period
    )
    levels = _build_levels(candles, prior_candles, tz, or_minutes)

    return candles, indicators, levels


def compute_replay_session(
    provider: DataProvider,
    symbol: str,
    session_date: date,
    settings: Settings,
    *,
    timeframe: str = "1m",
    ema_periods: tuple[int, ...] = DEFAULT_EMA_PERIODS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    or_minutes: int | None = None,
) -> ReplaySessionResponse:
    tz = getattr(settings, "tz", "America/New_York")
    or_min = or_minutes if or_minutes is not None else getattr(settings, "or_minutes", 5)

    candles, indicators, levels = _fetch_full_session(
        provider,
        symbol,
        session_date,
        tz,
        or_min,
        timeframe=timeframe,
        ema_periods=ema_periods,
        rsi_period=rsi_period,
    )

    return ReplaySessionResponse(
        symbol=symbol,
        session_date=session_date,
        candles=candles,
        indicators=indicators,
        levels=levels,
        meta=_session_meta(candles, or_min, tz, timeframe),
    )


def _session_meta(candles: list[Candle], or_min: int, tz: str, timeframe: str) -> dict:
    """Meta for a replay session; carries the provider's ``source_label`` (e.g.
    "SPY (proxy)") when the bars are a stand-in."""
    meta: dict = {"or_minutes": or_min, "tz": tz, "timeframe": timeframe}
    label = next((c.source_label for c in candles if c.source_label), None)
    if label:
        meta["source_label"] = label
    return meta


def _compute_indicators_for_candles(
    candles: list[Candle],
    prior_candles: list[Candle],
    tz: str,
    or_minutes: int,
) -> tuple[dict[str, list[float | None]], dict[str, list[float | None]]]:
    """Compute all indicators + levels for a pre-assembled candle list.

    Same logic as ``_fetch_full_session`` but takes candles directly rather than
    fetching them — used by the synthetic path which generates candles in-process.
    Returns ``(indicators, levels)`` aligned index-for-index with ``candles``.
    """
    indicators = _build_indicators(candles, tz)
    levels = _build_levels(candles, prior_candles, tz, or_minutes)
    return indicators, levels


def compute_scenario_session(
    provider: DataProvider,
    scenario: "ScenarioFull",
    settings: Settings,
    *,
    blind: bool,
) -> ScenarioSessionResponse:
    """Build the drill replay payload for a training scenario.

    Slice convention: **left-inclusive, right-exclusive** — ``candles[start_bar:end_bar]``.
    This is standard Python slice semantics. ``end_bar=None`` means "to the end of the
    session". The sliced ``candles`` array therefore has length ``end_bar - start_bar``
    (or ``len(full_candles) - start_bar`` when end_bar is None).

    Causal-alignment guarantee: indicators and levels are computed on the FULL session
    first, then the aligned arrays are sliced in lockstep with the candles. A value at
    sliced index ``i`` is identical to the value at full-session index ``start_bar + i``,
    and was computed using only ``full_candles[:start_bar + i + 1]``. Slicing never
    introduces lookahead.

    ``decision_bar`` translation: ``scenario.decision_bar`` is a full-session bar index.
    The response carries the **slice-local** index: ``scenario.decision_bar - start_bar``.
    Must satisfy ``0 <= local_decision_bar < len(sliced_candles)``; a violation is a
    scenario-authoring bug and raises ``ValueError`` (route -> HTTP 500).

    Blind mode: when ``blind=True``, ``symbol`` and ``session_date`` are set to ``None``
    and every candle timestamp is rebased so the ticker/date cannot be inferred from
    wall-clock time. The first bar of the slice is anchored at ``_BLIND_ANCHOR_UTC``
    (2001-01-01T13:30:00Z); subsequent bars preserve their original intra-session spacing
    (delta from the first bar). Annotations are passed through unchanged — they are
    teaching notes and must not be hidden.

    Raises:
        ValueError: if ``decision_bar`` is out of range for the sliced candles.
    """
    tz = getattr(settings, "tz", "America/New_York")
    or_minutes = getattr(settings, "or_minutes", 5)

    source_type = scenario.source_type
    # Normalise: source_type may be a ScenarioSource enum or a plain string.
    source_str = source_type.value if hasattr(source_type, "value") else str(source_type)

    if source_str == ScenarioSource.synthetic.value:
        # --- Synthetic path (task 6.3.1): generate tape deterministically. ---
        from app.models.training import Archetype
        from app.providers.mock.scenarios import generate_synthetic_session

        archetype_val = scenario.archetype
        archetype = (
            archetype_val
            if isinstance(archetype_val, Archetype)
            else Archetype(archetype_val)
        )
        synth_seed: int = getattr(scenario, "synth_seed", None) or 0
        synth_params: dict = getattr(scenario, "synth_params", None) or {}
        session_date_for_ts = getattr(scenario, "session_date", None)

        full_candles = generate_synthetic_session(
            archetype=archetype,
            seed=synth_seed,
            params=synth_params,
            session_date=session_date_for_ts,
        )
        # Synthetic sessions have no prior-day candles; pass an empty list so
        # PDH/PDL levels resolve to None (same as any session with no prior data).
        full_indicators, full_levels = _compute_indicators_for_candles(
            full_candles, [], tz, or_minutes
        )
        # Use "SYNTH" as the logical symbol; session_date stays as-is.
        symbol = "SYNTH"
        session_date = session_date_for_ts  # may be None for fallback-date scenarios

    else:
        # real and debrief both reference a (symbol, session_date) in candles_cache.
        symbol = scenario.symbol  # type: ignore[assignment]
        session_date = scenario.session_date  # type: ignore[assignment]

        # --- 1. Compute indicators on the FULL session first (causal guarantee). ---
        full_candles, full_indicators, full_levels = _fetch_full_session(
            provider, symbol, session_date, tz, or_minutes
        )

    n_full = len(full_candles)

    # --- 2. Determine slice bounds. ---
    start_bar: int = scenario.start_bar if scenario.start_bar is not None else 0
    raw_end: int | None = scenario.end_bar  # type: ignore[assignment]
    end_bar: int = raw_end if raw_end is not None else n_full

    if start_bar < 0 or start_bar > n_full:
        raise ValueError(
            f"start_bar={start_bar} is out of range for session with {n_full} bars"
        )
    if end_bar < start_bar or end_bar > n_full:
        raise ValueError(
            f"end_bar={end_bar} is out of range "
            f"(start_bar={start_bar}, n_full={n_full})"
        )

    # --- 3. Slice candles + all aligned arrays. ---
    sliced_candles: list[Candle] = full_candles[start_bar:end_bar]
    sliced_indicators = {k: v[start_bar:end_bar] for k, v in full_indicators.items()}
    sliced_levels = {k: v[start_bar:end_bar] for k, v in full_levels.items()}
    n_sliced = len(sliced_candles)

    # --- 4. Translate decision_bar to slice-local index. ---
    decision_bar_local: int | None = None
    raw_decision: int | None = scenario.decision_bar  # type: ignore[assignment]
    if raw_decision is not None:
        decision_bar_local = raw_decision - start_bar
        if not (0 <= decision_bar_local < n_sliced):
            raise ValueError(
                f"decision_bar={raw_decision} (global) translates to "
                f"local index {decision_bar_local}, which is out of range "
                f"[0, {n_sliced}) for the sliced candle array. "
                f"This is a scenario-authoring bug."
            )

    # --- 5. Blind mode: blank identifiers and rebase timestamps. ---
    response_symbol: str | None = symbol
    response_date: date | None = session_date

    if blind:
        response_symbol = None
        response_date = None
        # Rebase timestamps: keep intra-bar spacing, anchor first bar at _BLIND_ANCHOR_UTC.
        if sliced_candles:
            first_ts = sliced_candles[0].ts
            # Ensure first_ts is tz-aware for delta arithmetic.
            if first_ts.tzinfo is None:
                first_ts = first_ts.replace(tzinfo=timezone.utc)
            rebased: list[Candle] = []
            for c in sliced_candles:
                original_ts = c.ts
                if original_ts.tzinfo is None:
                    original_ts = original_ts.replace(tzinfo=timezone.utc)
                delta = original_ts - first_ts
                new_ts = _BLIND_ANCHOR_UTC + delta
                rebased.append(c.model_copy(update={"ts": new_ts, "symbol": "HIDDEN"}))
            sliced_candles = rebased

    # --- 6. Build response. ---
    drill_type_val = scenario.drill_type
    drill_type = (
        drill_type_val
        if isinstance(drill_type_val, DrillType)
        else DrillType(drill_type_val)
    )

    meta: dict = {
        "full_bar_count": n_full,
        "start_bar": start_bar,
        "end_bar": end_bar,
        "blind": blind,
        "or_minutes": or_minutes,
        "tz": tz,
    }
    if raw_decision is not None:
        meta["decision_bar_global"] = raw_decision

    return ScenarioSessionResponse(
        scenario_id=scenario.id,
        drill_type=drill_type,
        blind=blind,
        symbol=response_symbol,
        session_date=response_date,
        decision_bar=decision_bar_local,
        candles=sliced_candles,
        indicators=sliced_indicators,
        levels=sliced_levels,
        annotations=list(scenario.annotations),
        meta=meta,
    )
