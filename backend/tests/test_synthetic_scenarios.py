"""Tests for the deterministic synthetic scenario generators (M6 task 6.3.1).

Ten test groups:
1. test_same_seed_produces_identical_candles — seed determinism (all archetypes × seeds)
2. test_different_seeds_produce_different_candles — different seeds differ
3. test_gap_and_go_recognizably_gaps_up — first-bar open > baseline by gap_pct
4. test_failed_breakout_reverses_below_range — session close < consol-range low
5. test_chop_stays_in_range — max_high - min_low < 1.5 × atr_pct × open_price
6. test_trend_day_closes_near_session_extreme — close within top/bottom 15% of range
7. test_synthetic_scenario_via_session_endpoint — end-to-end API test
8. test_synthetic_indicators_are_causal — indicators computed on synthetic tape are causal
9. test_ohlc_invariants_hold — low <= open <= high, low <= close <= high, volume > 0
10. test_time_grid_matches_mock_provider — 78 bars, 5-min intervals, 09:30 ET start
"""
from __future__ import annotations

import math
from datetime import date, timedelta, timezone

import pytest

from app.models.training import Archetype
from app.providers.mock.scenarios import (
    N_BARS,
    BAR_MINUTES,
    REG_OPEN_MIN,
    generate_synthetic_session,
)

# All four "proper" archetypes (excluding custom which aliases trend_day).
ARCHETYPES = [
    Archetype.gap_and_go,
    Archetype.failed_breakout,
    Archetype.chop,
    Archetype.trend_day,
]

# A fixed test date (Wednesday in EDT → UTC-4, so 09:30 ET = 13:30 UTC).
TEST_DATE = date(2026, 6, 10)


# ---------------------------------------------------------------------------
# 1. Seed determinism
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize("seed", [0, 42, 12345])
def test_same_seed_produces_identical_candles(archetype, seed):
    """Same (archetype, seed, params) produces byte-identical Candle lists."""
    params = {"open_price": 50.0, "atr_pct": 0.025}
    candles_a = generate_synthetic_session(archetype, seed, params, TEST_DATE)
    candles_b = generate_synthetic_session(archetype, seed, params, TEST_DATE)

    assert len(candles_a) == len(candles_b) == N_BARS, (
        f"Expected {N_BARS} bars for archetype={archetype}, seed={seed}"
    )
    for i, (a, b) in enumerate(zip(candles_a, candles_b)):
        assert a == b, (
            f"Candle {i} differs on second call: "
            f"archetype={archetype.value}, seed={seed}\n"
            f"  first:  {a}\n"
            f"  second: {b}"
        )


# ---------------------------------------------------------------------------
# 2. Different seeds produce different candles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_different_seeds_produce_different_candles(archetype):
    """Seed 1 and seed 2 produce different Candle lists for the same archetype."""
    candles_1 = generate_synthetic_session(archetype, seed=1, session_date=TEST_DATE)
    candles_2 = generate_synthetic_session(archetype, seed=2, session_date=TEST_DATE)

    # At least one candle must differ.
    any_diff = any(a != b for a, b in zip(candles_1, candles_2))
    assert any_diff, (
        f"seed=1 and seed=2 produced identical candles for {archetype.value}"
    )


# ---------------------------------------------------------------------------
# 3. Gap-and-go recognizably gaps up
# ---------------------------------------------------------------------------

def test_gap_and_go_recognizably_gaps_up():
    """Bar-0 open must be above open_price by at least 80% of gap_pct.

    This tests that the "gap" is visually prominent — the opening print is
    well above the prior close (``open_price``), not buried in noise.
    """
    open_price = 100.0
    gap_pct = 0.05
    params = {"open_price": open_price, "gap_pct": gap_pct, "direction": "up"}
    candles = generate_synthetic_session(
        Archetype.gap_and_go, seed=7, params=params, session_date=TEST_DATE
    )

    bar0_open = candles[0].open
    expected_min_gap = open_price * gap_pct * 0.8

    assert bar0_open > open_price + expected_min_gap, (
        f"gap-and-go bar-0 open {bar0_open:.4f} should be > "
        f"{open_price + expected_min_gap:.4f} "
        f"(open_price={open_price}, gap_pct={gap_pct})"
    )


def test_gap_and_go_session_close_above_open():
    """Gap-and-go session must close higher than the gap-open for 'up' direction."""
    params = {"open_price": 100.0, "gap_pct": 0.04, "direction": "up"}
    candles = generate_synthetic_session(
        Archetype.gap_and_go, seed=42, params=params, session_date=TEST_DATE
    )
    session_open = candles[0].open
    session_close = candles[-1].close
    assert session_close > session_open, (
        f"gap-and-go should close above the gap-open: "
        f"open={session_open:.4f}, close={session_close:.4f}"
    )


# ---------------------------------------------------------------------------
# 4. Failed-breakout reverses below consolidation range
# ---------------------------------------------------------------------------

def test_failed_breakout_reverses_below_range():
    """Session close must be well below the consolidation range.

    Contract: find max high in first 10 bars (the consolidation phase),
    the session close must be at least 2 × ATR% below that high.
    """
    open_price = 100.0
    atr_pct = 0.02
    breakout_bar = 10
    params = {
        "open_price": open_price,
        "atr_pct": atr_pct,
        "breakout_bar": breakout_bar,
    }
    candles = generate_synthetic_session(
        Archetype.failed_breakout, seed=99, params=params, session_date=TEST_DATE
    )

    # Max high across consolidation bars (before the breakout).
    consol_high = max(c.high for c in candles[:breakout_bar])
    session_close = candles[-1].close
    two_atr = open_price * atr_pct * 2.0

    assert session_close < consol_high - two_atr, (
        f"failed-breakout session close {session_close:.4f} should be "
        f"< {consol_high - two_atr:.4f} "
        f"(consol_high={consol_high:.4f}, 2×ATR={two_atr:.4f})"
    )


def test_failed_breakout_has_prominent_upper_wick_at_breakout_bar():
    """The breakout bar must have an upper wick that extends well above its body."""
    open_price = 100.0
    atr_pct = 0.02
    breakout_bar = 10
    params = {
        "open_price": open_price,
        "atr_pct": atr_pct,
        "breakout_bar": breakout_bar,
    }
    candles = generate_synthetic_session(
        Archetype.failed_breakout, seed=7, params=params, session_date=TEST_DATE
    )

    c = candles[breakout_bar]
    body_hi = max(c.open, c.close)
    upper_wick = c.high - body_hi

    # Upper wick should be at least 1 × ATR% of open_price.
    assert upper_wick >= open_price * atr_pct * 1.0, (
        f"breakout bar upper wick {upper_wick:.4f} should be "
        f">= {open_price * atr_pct:.4f} (1 × ATR)"
    )


# ---------------------------------------------------------------------------
# 5. Chop stays in tight range
# ---------------------------------------------------------------------------

def test_chop_stays_in_range():
    """Chop session max_high - min_low must be < 1.5 × atr_pct × open_price."""
    open_price = 100.0
    atr_pct = 0.02
    params = {"open_price": open_price, "atr_pct": atr_pct}
    candles = generate_synthetic_session(
        Archetype.chop, seed=13, params=params, session_date=TEST_DATE
    )

    max_high = max(c.high for c in candles)
    min_low = min(c.low for c in candles)
    total_range = max_high - min_low
    limit = 1.5 * atr_pct * open_price

    assert total_range < limit, (
        f"chop session range {total_range:.4f} exceeds 1.5×ATR limit {limit:.4f} "
        f"(max_high={max_high:.4f}, min_low={min_low:.4f})"
    )


def test_chop_no_sustained_directional_move():
    """Chop session must not produce a persistent directional move.

    Criterion: the absolute net change (close - open) must be < 0.6 × ATR%
    of open_price. A trend day at 0.8× ATR would easily fail this.
    """
    open_price = 100.0
    atr_pct = 0.02
    params = {"open_price": open_price, "atr_pct": atr_pct}
    candles = generate_synthetic_session(
        Archetype.chop, seed=55, params=params, session_date=TEST_DATE
    )

    net_change = abs(candles[-1].close - candles[0].open)
    max_net = 0.6 * atr_pct * open_price

    assert net_change < max_net, (
        f"chop net change {net_change:.4f} indicates a trend "
        f"(max allowed: {max_net:.4f})"
    )


# ---------------------------------------------------------------------------
# 6. Trend day closes near session extreme
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction", ["up", "down"])
def test_trend_day_closes_near_session_extreme(direction):
    """Session close must be within the top (or bottom) 15% of the session range.

    For 'up': close >= session_low + 85% × session_range.
    For 'down': close <= session_high - 85% × session_range.
    """
    params = {
        "open_price": 100.0,
        "atr_pct": 0.02,
        "direction": direction,
    }
    candles = generate_synthetic_session(
        Archetype.trend_day, seed=17, params=params, session_date=TEST_DATE
    )

    session_high = max(c.high for c in candles)
    session_low = min(c.low for c in candles)
    session_range = session_high - session_low
    session_close = candles[-1].close

    if session_range == 0:
        pytest.skip("degenerate session with zero range")

    if direction == "up":
        threshold = session_low + 0.85 * session_range
        assert session_close >= threshold, (
            f"trend-day (up) close {session_close:.4f} should be >= "
            f"{threshold:.4f} (top 15% of range [{session_low:.4f}, {session_high:.4f}])"
        )
    else:
        threshold = session_high - 0.85 * session_range
        assert session_close <= threshold, (
            f"trend-day (down) close {session_close:.4f} should be <= "
            f"{threshold:.4f} (bottom 15% of range [{session_low:.4f}, {session_high:.4f}])"
        )


# ---------------------------------------------------------------------------
# 7. End-to-end via the scenario session endpoint
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(isolated_db):
    """TestClient with an isolated in-memory DB."""
    from app.main import create_app
    from fastapi.testclient import TestClient
    return TestClient(create_app())


def test_synthetic_scenario_via_session_endpoint(client):
    """POST a synthetic scenario, GET /session, assert 200 + candles returned.

    Confirms:
    - The route wires through to the slicer.
    - Synthetic tape is generated and sliced correctly.
    - Response has expected structure (candles, indicators, blind flag).
    """
    # 1. Create the synthetic scenario.
    create_payload = {
        "title": "gap-and-go synth e2e",
        "archetype": "gap-and-go",
        "source_type": "synthetic",
        "drill_type": "decision-point",
        "synth_seed": 42,
        "synth_params": {"open_price": 50.0, "gap_pct": 0.05},
        "start_bar": 0,
        "decision_bar": 5,
        "end_bar": 30,
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Clean gap continuation with volume confirmation.",
        },
        "blind_default": False,
    }
    r = client.post("/api/training/scenarios", json=create_payload)
    assert r.status_code == 200, f"POST /scenarios failed: {r.text}"
    scen_id = r.json()["id"]

    # 2. GET the session.
    r = client.get(f"/api/training/scenarios/{scen_id}/session")
    assert r.status_code == 200, f"GET /session failed: {r.text}"
    body = r.json()

    # 3. Structural assertions.
    assert "candles" in body, "response missing 'candles'"
    candles = body["candles"]
    assert len(candles) == 30, f"expected 30 sliced candles, got {len(candles)}"

    assert "indicators" in body
    for name, arr in body["indicators"].items():
        assert len(arr) == 30, f"indicator '{name}' length {len(arr)} != 30"

    # decision_bar=5 global, start_bar=0 → local=5
    assert body["decision_bar"] == 5

    # blind=False → symbol and session_date should be present (or "SYNTH" / None
    # for synthetic with no session_date — either is acceptable as long as no crash).
    assert body["blind"] is False


def test_synthetic_scenario_session_candles_are_deterministic(client):
    """Two GETs of the same synthetic session must return identical candles."""
    create_payload = {
        "title": "determinism check",
        "archetype": "trend-day",
        "source_type": "synthetic",
        "drill_type": "decision-point",
        "synth_seed": 1234,
        "synth_params": {"open_price": 80.0},
        "start_bar": 5,
        "end_bar": 25,
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Steady trend continuation.",
        },
        "blind_default": False,
    }
    r = client.post("/api/training/scenarios", json=create_payload)
    assert r.status_code == 200
    scen_id = r.json()["id"]

    r1 = client.get(f"/api/training/scenarios/{scen_id}/session")
    r2 = client.get(f"/api/training/scenarios/{scen_id}/session")
    assert r1.status_code == 200 and r2.status_code == 200

    candles_1 = r1.json()["candles"]
    candles_2 = r2.json()["candles"]
    assert candles_1 == candles_2, "Two GETs of the same synthetic session differ"


# ---------------------------------------------------------------------------
# 8. Indicators on synthetic tape are causal (no lookahead)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_synthetic_indicators_are_causal(archetype):
    """Indicators computed on synthetic tape must be causal (prefix-stable).

    For every bar i in [5, 20, 40, 60, 77]: the indicator value at position i
    from the full 78-bar session must equal the indicator value at position i
    from a session truncated to bars[:i+1]. A mismatch is a lookahead leak.
    """
    from app.indicators.ema import ema
    from app.indicators.rsi import rsi
    from app.indicators.vwap import vwap

    candles = generate_synthetic_session(
        archetype, seed=0, params={"open_price": 100.0}, session_date=TEST_DATE
    )
    assert len(candles) == N_BARS

    closes = [c.close for c in candles]
    full_ema9 = ema(closes, 9)
    full_rsi14 = rsi(closes, 14)
    full_vwap = vwap(candles)

    for i in [5, 20, 40, 60, 77]:
        trunc_closes = closes[: i + 1]
        trunc_candles = candles[: i + 1]

        trunc_ema9 = ema(trunc_closes, 9)
        trunc_rsi14 = rsi(trunc_closes, 14)
        trunc_vwap = vwap(trunc_candles)

        for name, full_arr, trunc_arr in [
            ("ema9", full_ema9, trunc_ema9),
            ("rsi14", full_rsi14, trunc_rsi14),
            ("vwap", full_vwap, trunc_vwap),
        ]:
            full_val = full_arr[i]
            trunc_val = trunc_arr[i]
            assert full_val == trunc_val, (
                f"LOOKAHEAD LEAK — {archetype.value} {name}[{i}]: "
                f"full={full_val!r} vs trunc={trunc_val!r}"
            )


# ---------------------------------------------------------------------------
# 9. OHLC invariants hold for every bar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ohlc_invariants_hold(archetype):
    """For every bar: low <= open <= high, low <= close <= high, volume > 0."""
    params = {"open_price": 100.0, "atr_pct": 0.02}
    candles = generate_synthetic_session(
        archetype, seed=42, params=params, session_date=TEST_DATE
    )

    violations = []
    for i, c in enumerate(candles):
        if not (c.low <= c.open <= c.high):
            violations.append(
                f"bar {i}: low={c.low} open={c.open} high={c.high} "
                f"(low <= open <= high violated)"
            )
        if not (c.low <= c.close <= c.high):
            violations.append(
                f"bar {i}: low={c.low} close={c.close} high={c.high} "
                f"(low <= close <= high violated)"
            )
        if c.volume <= 0:
            violations.append(f"bar {i}: volume={c.volume} is not positive")
        if c.low <= 0:
            violations.append(f"bar {i}: low={c.low} is not positive")

    assert not violations, (
        f"OHLC invariant violations for {archetype.value}:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 10. Time grid matches mock/synthetic.py's session discipline
# ---------------------------------------------------------------------------

def test_time_grid_matches_mock_provider():
    """Synthetic session time grid must be consistent with the mock provider.

    Checks:
    - Exactly N_BARS (78) candles.
    - Timeframe is '5m'.
    - All timestamps are UTC-aware.
    - Bar spacing is exactly BAR_MINUTES (5 minutes) between consecutive bars.
    - First bar opens at 09:30 ET (13:30 UTC in EDT for TEST_DATE in June).
    """
    from zoneinfo import ZoneInfo

    candles = generate_synthetic_session(
        Archetype.trend_day, seed=0, session_date=TEST_DATE
    )

    assert len(candles) == N_BARS, f"expected {N_BARS} candles, got {len(candles)}"

    for i, c in enumerate(candles):
        assert c.timeframe == "5m", f"bar {i}: timeframe={c.timeframe!r}, expected '5m'"
        assert c.ts.tzinfo is not None, f"bar {i}: timestamp has no timezone"

    # Spacing: every consecutive pair must be exactly 5 minutes apart.
    for i in range(1, len(candles)):
        delta = candles[i].ts - candles[i - 1].ts
        assert delta == timedelta(minutes=BAR_MINUTES), (
            f"bars {i-1}→{i}: spacing {delta} != {timedelta(minutes=BAR_MINUTES)}"
        )

    # First bar timestamp: 09:30 ET on TEST_DATE.
    # TEST_DATE = 2026-06-10 (June, EDT = UTC-4) → 09:30 ET = 13:30 UTC.
    ET = ZoneInfo("America/New_York")
    from datetime import datetime, time as dtime
    expected_open_et = datetime.combine(TEST_DATE, dtime(9, 30), tzinfo=ET)
    expected_open_utc = expected_open_et.astimezone(timezone.utc)

    bar0_ts = candles[0].ts
    if bar0_ts.tzinfo is None:
        bar0_ts = bar0_ts.replace(tzinfo=timezone.utc)

    assert bar0_ts == expected_open_utc, (
        f"bar 0 timestamp {bar0_ts} != expected 09:30 ET = {expected_open_utc}"
    )


def test_time_grid_without_session_date():
    """Fallback date (no session_date arg) produces the same N_BARS with 5m spacing."""
    candles = generate_synthetic_session(Archetype.chop, seed=0)

    assert len(candles) == N_BARS
    for i in range(1, len(candles)):
        delta = candles[i].ts - candles[i - 1].ts
        assert delta == timedelta(minutes=BAR_MINUTES), (
            f"fallback-date: bars {i-1}→{i} spacing {delta} != 5m"
        )


# ---------------------------------------------------------------------------
# Additional: params are respected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_open_price_param_respected(archetype):
    """open_price param must anchor the session to within ±30% of the requested price.

    The generators add noise and drift, but the fundamental price scale should
    be in the right ballpark. This is a smoke-test, not a tight constraint.
    """
    open_price = 200.0
    params = {"open_price": open_price, "atr_pct": 0.02}
    candles = generate_synthetic_session(
        archetype, seed=5, params=params, session_date=TEST_DATE
    )

    first_open = candles[0].open
    assert 0.7 * open_price <= first_open <= 1.3 * open_price, (
        f"{archetype.value}: first bar open {first_open:.2f} is outside "
        f"70%–130% of requested open_price {open_price}"
    )
