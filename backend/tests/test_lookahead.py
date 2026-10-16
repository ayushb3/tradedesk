"""THE no-lookahead guarantee.

Two levels of assurance:
  1. Pure functions are *prefix-stable*: f(xs)[:k] == f(xs[:k]).
  2. The full replay session's indicator/level arrays have a prefix equal to the
     arrays recomputed from a truncated candle set candles[:i+1]. Any mismatch is
     a lookahead leak; the failure message names the offending series + index.

M6 6.0.4 extension:
  3. Sliced scenario sessions preserve the same causal contract: indicator[i] in
     the sliced response equals indicator[start_bar+i] from the full session.
  4. Blind mode blanks symbol/session_date and rebases timestamps so the original
     date cannot be inferred from the candle timestamps.
"""
from __future__ import annotations

import math
from datetime import date, timezone
from types import SimpleNamespace

import pytest

from app.indicators import levels as lv
from app.indicators.ema import ema
from app.indicators.rsi import rsi
from app.indicators.vwap import vwap

from .conftest import PRIOR_DAY, TEST_DAY, mock_candles

# A non-trivial, reproducible input series for the pure-function checks.
XS = [math.sin(i / 5.0) * 10 + i * 0.3 + 50 for i in range(60)]


@pytest.mark.parametrize("period", [3, 9, 14, 21])
@pytest.mark.parametrize("k", [1, 5, 14, 15, 30, 45, 60])
def test_ema_prefix_stable(period, k):
    assert ema(XS, period)[:k] == ema(XS[:k], period), (
        f"ema(period={period}) not prefix-stable at k={k}"
    )


@pytest.mark.parametrize("period", [3, 14])
@pytest.mark.parametrize("k", [1, 5, 15, 16, 30, 45, 60])
def test_rsi_prefix_stable(period, k):
    assert rsi(XS, period)[:k] == rsi(XS[:k], period), (
        f"rsi(period={period}) not prefix-stable at k={k}"
    )


def _build_series(candles, prior_candles):
    """Recreate every indicator/level array from a candle list (raw, unrounded).

    Mirrors indicators.service.compute_replay_session but without the display
    rounding, so we compare exact causal values.
    """
    closes = [c.close for c in candles]
    series = {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "vwap": vwap(candles),
        "rsi14": rsi(closes, 14),
        "pdh": lv.pdh(candles, prior_candles),
        "pdl": lv.pdl(candles, prior_candles),
        "pmh": lv.pmh(candles),
        "pml": lv.pml(candles),
        "day_high": lv.day_high(candles),
        "day_low": lv.day_low(candles),
        "or_high": lv.or_high(candles, 5),
        "or_low": lv.or_low(candles, 5),
    }
    return series


def _assert_no_lookahead(full_series, sliced_series, start_bar, label=""):
    """Assert that every value in ``sliced_series`` equals the corresponding
    value in ``full_series[start_bar:]``.

    This is the core causal test for sliced sessions: sliced index i must equal
    full-session index start_bar+i (computed on full_candles[:start_bar+i+1]).
    Any mismatch is a lookahead or alignment bug.
    """
    for name, sliced_arr in sliced_series.items():
        full_arr = full_series[name]
        for i, sliced_val in enumerate(sliced_arr):
            global_i = start_bar + i
            full_val = full_arr[global_i]
            assert sliced_val == full_val, (
                f"{label}CAUSAL ALIGNMENT FAILURE in series '{name}' at "
                f"slice index {i} (global {global_i}): "
                f"sliced={sliced_val!r} vs full[{global_i}]={full_val!r}"
            )


# Indices spanning premarket, the open, the OR window, midday, close, after-hours.
# First regular bar is index 330 (09:30 ET); OR window (5m) covers 330..334.
_INDICES = [0, 100, 329, 330, 334, 335, 400, 719, 720, 800, 959]


def test_session_arrays_have_no_lookahead():
    candles = mock_candles("AAPL", TEST_DAY)
    prior = mock_candles("AAPL", PRIOR_DAY)
    assert len(candles) == 960

    full = _build_series(candles, prior)

    for i in _INDICES:
        truncated = _build_series(candles[: i + 1], prior)
        for name, full_arr in full.items():
            trunc_arr = truncated[name]
            prefix = full_arr[: i + 1]
            assert len(trunc_arr) == i + 1
            for j, (a, b) in enumerate(zip(prefix, trunc_arr)):
                assert a == b, (
                    f"LOOKAHEAD LEAK in series '{name}' at bar {j} when truncating "
                    f"candles to [:{i + 1}]: full={a!r} vs truncated={b!r}"
                )


# ---------------------------------------------------------------------------
# M6 6.0.4: scenario session slicing tests
# ---------------------------------------------------------------------------

def _make_scenario(
    start_bar: int,
    end_bar: int,
    decision_bar: int | None = None,
    source_type: str = "real",
    drill_type: str = "decision-point",
    blind_default: bool = False,
) -> object:
    """Build a minimal duck-typed ScenarioFull-compatible object.

    Uses SimpleNamespace so these tests run even before task 6.0.2 lands the real
    ScenarioFull class; the slicer only reads a known set of attributes.
    """
    return SimpleNamespace(
        id=1,
        title="Test scenario",
        archetype="custom",
        source_type=source_type,
        drill_type=drill_type,
        symbol="AAPL",
        session_date=TEST_DAY,
        start_bar=start_bar,
        end_bar=end_bar,
        decision_bar=decision_bar,
        annotations=[],
        answer_key=None,
        checklist_template_id=None,
        source_trade_id=None,
        blind_default=blind_default,
        active=True,
    )


def _make_provider_and_settings():
    """Return a (MockProvider, Settings) pair for test use."""
    from app.config import Settings
    from app.providers.mock import MockProvider

    s = Settings()
    return MockProvider(s), s


def test_scenario_session_sliced_range_causal():
    """Indicator values in a sliced scenario session must equal the full-session
    values at the corresponding global indices — the causal invariant from
    _assert_no_lookahead.

    Slice: bars 330..530 (regular-session open through midday), a non-trivial
    range that crosses the OR window boundary and contains both premarket-derived
    levels (PDH/PDL) and live rolling levels (day_high/day_low).
    """
    from app.indicators.service import compute_scenario_session

    START = 330  # first regular-session bar (09:30 ET)
    END = 530    # 200 bars into the session

    provider, settings = _make_provider_and_settings()
    scenario = _make_scenario(start_bar=START, end_bar=END, decision_bar=350)

    result = compute_scenario_session(provider, scenario, settings, blind=False)

    # --- length ---
    assert len(result.candles) == END - START, (
        f"expected {END - START} candles, got {len(result.candles)}"
    )
    for name, arr in result.indicators.items():
        assert len(arr) == END - START, (
            f"indicator '{name}' length {len(arr)} != slice length {END - START}"
        )
    for name, arr in result.levels.items():
        assert len(arr) == END - START, (
            f"level '{name}' length {len(arr)} != slice length {END - START}"
        )

    # --- decision_bar translated to local index ---
    assert result.decision_bar == 350 - START, (
        f"expected decision_bar={350 - START}, got {result.decision_bar}"
    )

    # --- causal alignment: sliced[i] == full[START+i] ---
    # Build the reference full-session arrays (rounded, same as service.py).
    from app.indicators.service import _fetch_full_session

    full_candles, full_indicators, full_levels = _fetch_full_session(
        provider, "AAPL", TEST_DAY,
        tz=getattr(settings, "tz", "America/New_York"),
        or_minutes=getattr(settings, "or_minutes", 5),
    )

    _assert_no_lookahead(full_indicators, result.indicators, START, label="indicators: ")
    _assert_no_lookahead(full_levels, result.levels, START, label="levels: ")

    # --- non-blind: symbol and session_date are populated ---
    assert result.symbol == "AAPL"
    assert result.session_date == TEST_DAY


def test_scenario_session_full_bar_count_in_meta():
    """meta.full_bar_count must equal the total number of bars in the session,
    and start_bar / end_bar must be recorded accurately."""
    from app.indicators.service import compute_scenario_session

    provider, settings = _make_provider_and_settings()
    scenario = _make_scenario(start_bar=100, end_bar=400)

    result = compute_scenario_session(provider, scenario, settings, blind=False)

    assert result.meta["full_bar_count"] == 960  # mock AAPL session is 960 bars
    assert result.meta["start_bar"] == 100
    assert result.meta["end_bar"] == 400


def test_scenario_session_decision_bar_out_of_range_raises():
    """A decision_bar that falls outside the slice must raise ValueError.

    This is a scenario-authoring bug — the route maps it to HTTP 500 deliberately
    to surface the misconfiguration loudly rather than silently clamping.
    """
    from app.indicators.service import compute_scenario_session

    provider, settings = _make_provider_and_settings()
    # decision_bar=50 is below start_bar=330 -> local index -280 -> out of range
    scenario = _make_scenario(start_bar=330, end_bar=430, decision_bar=50)

    with pytest.raises(ValueError, match="decision_bar"):
        compute_scenario_session(provider, scenario, settings, blind=False)


def test_scenario_session_synthetic_returns_candles():
    """Synthetic source_type must generate candles and return a valid session.

    Task 6.3.1 removes the NotImplementedError stub. This test verifies that
    the synthetic path generates candles, runs the indicator pipeline, and
    returns a properly-sliced ScenarioSessionResponse.
    """
    from app.indicators.service import compute_scenario_session

    provider, settings = _make_provider_and_settings()
    # Synthetic scenario: needs synth_seed + synth_params attributes.
    scenario = SimpleNamespace(
        id=99,
        title="Synthetic lookahead test",
        archetype="gap-and-go",
        source_type="synthetic",
        drill_type="decision-point",
        symbol=None,
        session_date=None,
        start_bar=0,
        end_bar=30,
        decision_bar=10,
        annotations=[],
        answer_key=None,
        checklist_template_id=None,
        source_trade_id=None,
        blind_default=False,
        active=True,
        synth_seed=42,
        synth_params={"open_price": 50.0},
    )

    result = compute_scenario_session(provider, scenario, settings, blind=False)

    assert len(result.candles) == 30, f"expected 30 candles, got {len(result.candles)}"
    for name, arr in result.indicators.items():
        assert len(arr) == 30, f"indicator '{name}' length {len(arr)} != 30"
    # decision_bar=10 global with start_bar=0 → local index 10
    assert result.decision_bar == 10


def test_scenario_session_blind_mode():
    """Blind mode must:
    - Set symbol=None and session_date=None in the response.
    - Rebase candle timestamps so none of them match the original session dates.
    - Leave annotations unchanged (they are teaching notes, not identifiers).
    """
    from app.indicators.service import compute_scenario_session

    START = 330
    END = 430

    provider, settings = _make_provider_and_settings()
    scenario = _make_scenario(start_bar=START, end_bar=END, decision_bar=360)

    # Collect original timestamps for comparison.
    candles_ref = mock_candles("AAPL", TEST_DAY)[START:END]
    original_dates = {c.ts.date() for c in candles_ref}

    result = compute_scenario_session(provider, scenario, settings, blind=True)

    # --- identifiers blanked ---
    assert result.symbol is None, "blind mode must blank symbol"
    assert result.session_date is None, "blind mode must blank session_date"

    # --- timestamps rebased (no candle ts should match the original session date) ---
    blind_dates = set()
    for c in result.candles:
        ts = c.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        blind_dates.add(ts.date())

    assert blind_dates.isdisjoint(original_dates), (
        f"blind mode leaked original session dates: "
        f"rebased={blind_dates}, original={original_dates}"
    )

    # --- length and decision_bar still correct ---
    assert len(result.candles) == END - START
    assert result.decision_bar == 360 - START

    # --- indicator/level alignment still holds after rebasing ---
    # (blind mode only changes timestamps and symbol on candles, not the arrays)
    from app.indicators.service import _fetch_full_session

    full_candles, full_indicators, full_levels = _fetch_full_session(
        provider, "AAPL", TEST_DAY,
        tz=getattr(settings, "tz", "America/New_York"),
        or_minutes=getattr(settings, "or_minutes", 5),
    )
    _assert_no_lookahead(full_indicators, result.indicators, START, label="blind indicators: ")
    _assert_no_lookahead(full_levels, result.levels, START, label="blind levels: ")
