"""Excursion data-quality check (suspect wicks) and the after-exit return-to-entry probe.

Synthetic bars and made-up tickers only; no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.excursions import (
    WICK_MIN_ABS, WICK_REL, back_to_entry, bounded_hold_bars, check_hold_wicks, compute_excursions,
)
from app.models.market import Candle

UTC = timezone.utc
T0 = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)
OPEN, CLOSE = T0 + timedelta(minutes=1), T0 + timedelta(minutes=2, seconds=30)
AVG = 2.50
TOL = max(WICK_MIN_ABS, WICK_REL * AVG)  # 0.50
FILLS = [2.50, 2.68]


def bar(minute: int, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(symbol="ZORP_261013_C_25", ts=T0 + timedelta(minutes=minute),
                  open=o, high=h, low=lo, close=c, volume=5.0)


def check(bars):
    return check_hold_wicks(bars, fill_prices=FILLS, avg_in=AVG, open_ts=OPEN, close_ts=CLOSE)


def test_clean_hold_is_ok_and_untouched():
    bars = [bar(1, 2.50, 2.60, 2.45, 2.55), bar(2, 2.55, 2.70, 2.52, 2.68)]
    r = check(bars)
    assert r.quality == "ok" and r.suspect == []
    assert r.bars == bars


def test_low_wick_with_body_near_fills_is_suspect_and_trimmed():
    bars = [bar(1, 2.50, 2.60, 1.25, 2.52), bar(2, 2.55, 2.70, 2.52, 2.68)]
    r = check(bars)
    assert r.quality == "suspect_wicks"
    assert [(w.side, w.raw, w.trimmed) for w in r.suspect] == [("low", 1.25, 2.50)]
    assert r.bars[0].low == 2.50 and r.bars[0].high == 2.60
    assert bars[0].low == 1.25  # input not mutated


def test_high_wick_is_suspect():
    r = check([bar(1, 2.50, 4.00, 2.45, 2.60)])
    assert [(w.side, w.trimmed) for w in r.suspect] == [("high", 2.60)]


def test_boundary_exactly_tolerance_beyond_is_not_suspect():
    lo = min(FILLS) - TOL  # 2.00
    assert not check([bar(1, 2.50, 2.60, lo, 2.52)]).suspect
    assert check([bar(1, 2.50, 2.60, lo - 0.01, 2.52)]).suspect
    hi = max(FILLS) + TOL  # 3.18
    assert not check([bar(1, 2.60, hi, 2.55, 2.65)]).suspect
    assert check([bar(1, 2.60, hi + 0.01, 2.55, 2.65)]).suspect


def test_body_that_also_left_the_fills_is_a_real_move():
    # low far under the fills AND the close is far under them too: not a wick.
    assert not check([bar(1, 2.50, 2.60, 1.25, 1.40)]).suspect


def test_bars_outside_the_hold_are_ignored():
    outside = bar(5, 2.50, 2.60, 1.00, 2.52)
    r = check([bar(1, 2.50, 2.60, 2.45, 2.55), outside])
    assert r.suspect == [] and r.bars[1] is outside


def test_cheap_contract_uses_absolute_floor():
    r = check_hold_wicks(
        [bar(1, 0.20, 0.22, 0.10, 0.21)], fill_prices=[0.20, 0.22], avg_in=0.20,
        open_ts=OPEN, close_ts=CLOSE,
    )
    # tol = max(0.05, 0.04) = 0.05; low 0.10 is 0.10 under 0.20 -> suspect
    assert r.quality == "suspect_wicks"


LONG_OPEN = T0 + timedelta(minutes=1, seconds=20)
LONG_CLOSE = T0 + timedelta(minutes=6, seconds=30)
LONG_FILLS = [(LONG_OPEN, 2.50), (LONG_CLOSE, 2.68)]


def long_bars(wick_low=None):
    """Entry minute 1, interior minutes 2..5, exit minute 6 (mid-minute fills)."""
    return [
        bar(1, 2.50, 2.60, 2.45, 2.55),
        bar(2, 2.55, 2.62, 2.50, 2.60),
        bar(3, 2.60, 2.66, wick_low if wick_low is not None else 2.55, 2.62),
        bar(4, 2.62, 2.70, 2.58, 2.66),
        bar(5, 2.66, 2.72, 2.60, 2.68),
        bar(6, 2.68, 2.70, 2.62, 2.66),
    ]


def measure(bars, **over):
    kw = dict(avg_in=AVG, qty=1, risk=75.0, open_ts=LONG_OPEN, close_ts=LONG_CLOSE, fills=LONG_FILLS)
    kw.update(over)
    return compute_excursions(bars, **kw)


def test_interior_wick_is_trimmed_and_phantom_stop_cross_removed():
    bars = long_bars(wick_low=1.25)
    ex = measure(bars)
    assert ex.wicks.quality == "suspect_wicks"
    assert ex.raw_hold.crossed_stop and ex.raw_hold.mae_r < -1.0
    assert not ex.hold.crossed_stop and ex.hold.mae_r == 0.0
    assert not ex.hold.short_hold


def test_exit_mid_minute_post_exit_dive_is_not_mae_or_stop():
    bars = long_bars()
    # the exit minute dives well under entry AFTER the exit fill (47 s of trading after it)
    bars[5] = bar(6, 2.68, 2.70, 1.00, 1.20)
    ex = measure(bars)
    assert ex.hold.mae_r == 0.0 and ex.hold.mae_ts is None
    assert not ex.hold.crossed_stop
    # the old whole-bar behaviour would have counted it
    legacy = measure(bars, fills=None)
    assert legacy.hold.crossed_stop and legacy.hold.mae_r < -1.0


def test_entry_mid_minute_pre_entry_spike_is_not_mfe():
    bars = long_bars()
    bars[0] = bar(1, 2.50, 4.00, 2.45, 2.55)  # spike before the entry fill
    ex = measure(bars)
    assert ex.hold.mfe_per_contract == pytest.approx(0.22)  # best interior high, not the pre-entry 4.00


def test_edge_bars_are_bounded_by_their_fills():
    bars, touched = bounded_hold_bars(
        long_bars(), fills=LONG_FILLS, open_ts=LONG_OPEN, close_ts=LONG_CLOSE
    )
    assert touched == 6 and len(bars) == 6
    assert (bars[0].low, bars[0].high) == (2.50, 2.50)  # entry minute = the entry fill
    assert (bars[-1].low, bars[-1].high) == (2.68, 2.68)


def test_sub_two_minute_hold_is_short_hold_from_fills_alone():
    o, c = T0 + timedelta(minutes=1, seconds=20), T0 + timedelta(minutes=2, seconds=13)
    bars = [bar(1, 2.50, 3.50, 1.20, 2.55), bar(2, 2.55, 2.70, 0.50, 0.80)]
    ex = compute_excursions(
        bars, avg_in=2.50, qty=1, risk=75.0, open_ts=o, close_ts=c,
        fills=[(o, 2.50), (c, 2.68)],
    )
    h = ex.hold
    assert h.short_hold and h.short_hold_bars == 2
    assert h.mae_r == 0.0 and not h.crossed_stop
    assert h.mfe_per_contract == pytest.approx(0.18)


def test_open_position_is_never_short_hold():
    o = T0 + timedelta(minutes=1, seconds=20)
    ex = compute_excursions(
        [bar(1, 2.50, 2.60, 2.40, 2.55)], avg_in=2.50, qty=1, risk=75.0, open_ts=o,
        close_ts=None, fills=[(o, 2.50)],
    )
    assert not ex.hold.short_hold


def test_longer_hold_still_uses_interior_bars():
    bars = long_bars()
    bars[2] = bar(3, 2.60, 2.66, 2.20, 2.40)  # a real interior dip (body too)
    ex = measure(bars)
    assert ex.wicks.quality == "ok"
    assert ex.hold.mae_per_contract == pytest.approx(-0.30)
    assert ex.hold.mae_ts == T0 + timedelta(minutes=3)


def test_back_to_entry_after_a_loss():
    bars = [bar(3, 2.0, 2.1, 1.9, 2.0), bar(4, 2.0, 2.3, 1.95, 2.2), bar(6, 2.2, 2.55, 2.1, 2.5)]
    r = back_to_entry(bars, avg_in=2.50, exit_px=2.0, exit_ts=T0 + timedelta(minutes=3))
    assert r.returned_at == T0 + timedelta(minutes=6) and r.bars == 3


def test_back_to_entry_never_in_window():
    bars = [bar(3, 2.0, 2.1, 1.9, 2.0), bar(4, 2.0, 2.3, 1.95, 2.2)]
    r = back_to_entry(bars, avg_in=2.50, exit_px=2.0, exit_ts=T0 + timedelta(minutes=3))
    assert r.returned_at is None and r.bars == 2 and not r.complete


def test_back_to_entry_after_a_win_means_it_fell_back():
    bars = [bar(3, 2.8, 2.9, 2.7, 2.8), bar(4, 2.7, 2.75, 2.45, 2.5)]
    r = back_to_entry(bars, avg_in=2.50, exit_px=2.8, exit_ts=T0 + timedelta(minutes=3))
    assert r.returned_at == T0 + timedelta(minutes=4)


def test_back_to_entry_without_bars():
    r = back_to_entry([], avg_in=2.5, exit_px=2.0, exit_ts=T0)
    assert r.bars == 0 and r.returned_at is None


# ------------------------------------------------------------------- the API
from tests.test_desk_api import client, rules_file  # noqa: E402,F401


def test_route_carries_quality_and_after_exit(client):
    d = client.get("/api/days/2026-10-13").json()["positions"][0]
    j = client.get(f"/api/trades/{d['trade_id']}/excursions").json()
    assert j["quality"] in ("ok", "suspect_wicks")
    assert j["clamped"] is (j["quality"] == "suspect_wicks")
    if j["quality"] == "ok":
        assert j["quality_reason"] is None and j["hold"]["raw_mae_r"] is None
    ae = j["after_exit"]
    assert ae["window_min"] == 30 and "dollar" not in str(ae).lower()


def _build(monkeypatch, bars, open_ts, close_ts):
    from types import SimpleNamespace

    from app.ingest import trade_excursions as te
    rules = SimpleNamespace(timezone="America/Los_Angeles")
    monkeypatch.setattr(te, "_pt", lambda ts, r: "06:46 PT")
    fills = [
        SimpleNamespace(side="B", qty=1, px=2.50, ts=open_ts),
        SimpleNamespace(side="S", qty=1, px=2.68, ts=close_ts),
    ]
    a = SimpleNamespace(fills=fills, closed=True, avg_in=2.50, qty_in=1, risk=75.0, r_basis="assumed")
    lp = SimpleNamespace(trade_id=1, label="ZORP")
    return te.build_trade_excursions(
        lp, a, rules, SimpleNamespace(bars=bars, synthetic=False, method="provider")
    )


def test_build_flags_and_clamps_a_wick(monkeypatch):
    out = _build(monkeypatch, long_bars(wick_low=1.25), LONG_OPEN, LONG_CLOSE)
    assert out.quality == "suspect_wicks" and out.clamped and out.suspect_wicks == 1
    assert out.quality_reason and "bodies" in out.quality_reason
    assert out.hold.mae_r == 0.0 and out.hold.raw_mae_r < -1.0
    assert out.hold.crossed_stop is False


def test_build_short_hold_quality(monkeypatch):
    o, c = T0 + timedelta(minutes=1, seconds=20), T0 + timedelta(minutes=2, seconds=13)
    bars = [bar(1, 2.50, 2.60, 2.45, 2.55), bar(2, 2.55, 2.70, 0.50, 0.80)]
    out = _build(monkeypatch, bars, o, c)
    assert out.quality == "short_hold" and not out.clamped
    assert out.quality_reason.startswith("held under 2 min; 1-min bars can't resolve the path")
    assert out.hold.mae_r == 0.0 and out.hold.crossed_stop is False


def test_rollup_fact_uses_the_same_bounded_hold(monkeypatch):
    from types import SimpleNamespace

    from app.ingest import excursion_rollup as er

    bars = long_bars()
    bars[5] = bar(6, 2.68, 2.70, 1.00, 1.20)  # post-exit dive in the exit minute
    fills = [
        SimpleNamespace(side="B", qty=1, px=2.50, ts=LONG_OPEN, pnl=None),
        SimpleNamespace(side="S", qty=1, px=2.68, ts=LONG_CLOSE, pnl=18.0),
    ]
    a = SimpleNamespace(fills=fills, closed=True, pnl=18.0, avg_in=2.50, qty_in=1, risk=75.0)
    monkeypatch.setattr(
        er, "fetch_bars",
        lambda *a_, **k: (SimpleNamespace(bars=bars), SimpleNamespace(descriptor="ZORP"), None),
    )
    fact = er.fact_for_position(SimpleNamespace(trade_id=1), a)
    assert fact is not None and fact.crossed_stop is False
