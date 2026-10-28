"""Excursion rollup (#52): held past stop / exited early / median drawdown, and how the
new pattern codes flow to patterns, get_day and the lesson map.

Synthetic bars and made-up tickers only; no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.excursions import (
    ExcursionFact,
    beyond_exit_r,
    excursion_codes,
    exited_early,
    rollup_excursions,
)
from app.ingest.option_bars import OptionBars
from app.models.market import Candle

UTC = timezone.utc
T0 = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)


def _bar(ts, lo, hi, close=None):
    return Candle(
        symbol="ZORP_261013_C_25", ts=ts, open=(lo + hi) / 2, high=hi, low=lo,
        close=close if close is not None else (lo + hi) / 2, volume=10.0,
    )


def _fact(pnl, risk=100.0, crossed=False, dd=0, beyond=None, tid=1):
    return ExcursionFact(tid, pnl, risk, crossed, dd, beyond)


# --------------------------------------------------------- exited early (pure)

# exit at 2.00, 2 contracts => $200 per 1.00 of premium; risk $100 => 1R = 0.50 above the exit
EXIT_KW = dict(exit_px=2.00, qty=2, risk=100.0, exit_ts=T0)


def test_beyond_exit_r_is_measured_from_exit_price_not_cost():
    bars = [_bar(T0 + timedelta(minutes=3), 1.90, 2.50)]
    # (2.50 - 2.00) * 200 / 100 = 1.0R from the exit (avg cost is irrelevant here)
    assert beyond_exit_r(bars, **EXIT_KW) == pytest.approx(1.0)


def test_exited_early_at_the_one_r_threshold_just_under_and_just_over():
    at = [_bar(T0 + timedelta(minutes=3), 1.9, 2.50)]
    under = [_bar(T0 + timedelta(minutes=3), 1.9, 2.49)]
    over = [_bar(T0 + timedelta(minutes=3), 1.9, 2.51)]
    for bars, expect in ((at, True), (under, False), (over, True)):
        r = beyond_exit_r(bars, **EXIT_KW)
        assert exited_early(_fact(50.0, beyond=r)) is expect


def test_exited_early_window_is_30_minutes_after_exit():
    inside = [_bar(T0 + timedelta(minutes=29), 2.0, 3.0)]
    outside = [_bar(T0 + timedelta(minutes=30), 2.0, 3.0)]
    before = [_bar(T0 - timedelta(minutes=1), 2.0, 3.0)]
    assert beyond_exit_r(inside, **EXIT_KW) == pytest.approx(2.0)
    assert beyond_exit_r(outside, **EXIT_KW) is None
    assert beyond_exit_r(before, **EXIT_KW) is None


def test_beyond_exit_r_never_negative_and_needs_risk():
    bars = [_bar(T0 + timedelta(minutes=1), 1.0, 1.5)]
    assert beyond_exit_r(bars, **EXIT_KW) == 0.0
    assert beyond_exit_r(bars, **{**EXIT_KW, "risk": None}) is None


def test_exited_early_only_for_winners():
    assert not exited_early(_fact(-10.0, beyond=3.0))
    assert not exited_early(_fact(0.0, beyond=3.0))
    assert not exited_early(_fact(10.0, beyond=None))
    assert exited_early(_fact(10.0, beyond=1.0))


# ------------------------------------------------------------ held past stop

def test_held_past_stop_recovered_vs_not_and_cost():
    facts = [
        _fact(40.0, crossed=True, tid=1),       # crossed, recovered
        _fact(0.0, crossed=True, tid=2),        # break-even counts as recovered
        _fact(-150.0, crossed=True, tid=3),     # lost 50 more than the $100 stop
        _fact(-60.0, crossed=True, tid=4),      # bounced: lost 40 less than the stop
        _fact(-20.0, crossed=False, tid=5),     # never crossed: not counted
    ]
    r = rollup_excursions(facts)
    assert (r.held_past_stop, r.recovered, r.not_recovered) == (4, 2, 2)
    # cost = pnl + risk over the non-recovered: (-150+100) + (-60+100) = -10
    assert r.not_recovered_cost_usd == pytest.approx(-10.0)
    # R: (-50/100) + (40/100) = -0.1
    assert r.not_recovered_cost_r == pytest.approx(-0.1)


def test_no_held_past_stop_costs_zero():
    r = rollup_excursions([_fact(10.0), _fact(-5.0)])
    assert r.held_past_stop == 0 and r.not_recovered_cost_usd == 0.0
    assert r.not_recovered_cost_r == 0.0


# ------------------------------------------------- drawdown, skipped, counts

def test_median_drawdown_minutes():
    r = rollup_excursions([_fact(1, dd=0), _fact(1, dd=4), _fact(1, dd=10)])
    assert r.median_drawdown_minutes == 4
    r = rollup_excursions([_fact(1, dd=2), _fact(1, dd=5), _fact(1, dd=6), _fact(1, dd=9)])
    assert r.median_drawdown_minutes == 5.5
    assert rollup_excursions([]).median_drawdown_minutes is None


def test_skipped_trades_are_counted_and_excluded():
    facts = [_fact(50.0, crossed=True, dd=3, beyond=2.0), _fact(-5.0, dd=7)]
    r = rollup_excursions(facts, n_trades=5)
    assert (r.n_trades, r.n_measured, r.n_skipped) == (5, 2, 3)
    assert r.held_past_stop == 1 and r.exited_early == 1 and r.winners_checked == 1
    assert r.median_drawdown_minutes == 5


def test_codes():
    assert excursion_codes(_fact(50.0, crossed=True, beyond=1.5)) == [
        "held_past_stop", "exited_early",
    ]
    assert excursion_codes(_fact(-5.0)) == []
    assert excursion_codes(None) == []


# -------------------------------------------------- flow: patterns / get_day / lessons

def _ctx(tmp_path, mode):
    from app.db.connection import get_engine
    from app.desk_mcp import mock, tools

    path = tmp_path / f"{mode}.db"
    mock.build_mock_db(path)
    return tools.Ctx(mode, str(path), get_engine(str(path)), mock.mock_rules())


def _bars(open_ts, close_ts, *, avg, exit_px, low, dd_bars, post_high):
    """One-minute bars: hold bars (first ``dd_bars`` close under cost), exit bar, post-exit."""
    out = []
    n = int((close_ts - open_ts).total_seconds() // 60)
    for i in range(n):
        under = i < dd_bars
        out.append(_bar(
            open_ts + timedelta(minutes=i), low if i == 0 else avg - 0.01, avg + 0.05,
            avg - 0.02 if under else avg + 0.02,
        ))
    out.append(_bar(close_ts, exit_px, exit_px, exit_px))
    out.append(_bar(close_ts + timedelta(minutes=5), exit_px - 0.05, post_high, exit_px))
    return OptionBars(out, True, "provider")


def _span(p):
    f = p["fills"]
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return parse(f[0]["ts"]), parse(f[-1]["ts"])


@pytest.fixture
def rigged(monkeypatch, tmp_path):
    """Mock DB where bars come from a patched ``fetch_bars``.

    10-08 ZORP winner (1.00 x2 -> 1.60, risk $60): crosses the 0.70 stop, runs exactly
    1R (0.30 of premium) past the exit.  10-12 ACME loser (3.00 x2 -> 1.00, risk $180):
    crosses, does not recover.  10-13 ZORP winner (1.20 x3 -> 1.50): no cross, just
    under 1R.  10-09 NIMB and 10-13 ACME have no bars (skipped).
    """
    from app.desk_mcp import tools
    from app.ingest import excursion_rollup as er
    from app.ingest.query import get_trade_option_contract

    ctx = _ctx(tmp_path, "mock")
    specs: dict[int, OptionBars] = {}

    def fake_fetch(trade_id, provider=None, engine=None, bars_mode="provider"):
        return specs.get(trade_id), get_trade_option_contract(trade_id, engine=engine), "x"

    monkeypatch.setattr(er, "fetch_bars", fake_fetch)

    def first(day):
        return tools.get_day(ctx, day)["positions"][0]

    p = first("2026-10-08")
    specs[p["trade_id"]] = _bars(*_span(p), avg=1.00, exit_px=1.60, low=0.65, dd_bars=3,
                                 post_high=1.90)
    p = first("2026-10-12")
    specs[p["trade_id"]] = _bars(*_span(p), avg=3.00, exit_px=1.00, low=2.0, dd_bars=5,
                                 post_high=1.0)
    p = first("2026-10-13")
    specs[p["trade_id"]] = _bars(*_span(p), avg=1.20, exit_px=1.50, low=1.19, dd_bars=4,
                                 post_high=1.85)
    er.clear_fact_memo()  # the rigged bars replace what the loads above memoized
    return ctx


def test_get_day_has_codes_tags_and_counts(rigged):
    from app.desk_mcp import tools

    d = tools.get_day(rigged, "2026-10-08")
    tags = {t["code"]: t for t in d["positions"][0]["tags"]}
    assert tags["held_past_stop"]["text"] == "held past stop, recovered"
    assert "exited_early" in tags
    roll = d["excursion_rollup"]
    assert roll["measured"] and roll["summary"] == "1 measured / 1 trades"
    assert roll["held_past_stop"]["count"] == 1 and roll["held_past_stop"]["recovered"] == 1
    assert roll["exited_early"]["count"] == 1
    assert roll["median_drawdown_minutes"] == 3

    d = tools.get_day(rigged, "2026-10-12")
    t = {x["code"]: x for x in d["positions"][0]["tags"]}
    assert t["held_past_stop"]["kind"] == "r" and "exited_early" not in t
    assert d["excursion_rollup"]["held_past_stop"]["not_recovered"] == 1


def test_get_day_skipped_when_no_bars(rigged):
    from app.desk_mcp import tools

    d = tools.get_day(rigged, "2026-10-09")
    assert d["excursion_rollup"]["summary"] == "0 measured / 1 trades"
    assert d["excursion_rollup"]["n_skipped"] == 1
    assert all(x["code"] not in ("held_past_stop", "exited_early")
               for x in d["positions"][0]["tags"])


def test_patterns_rollup_and_tag_counts(rigged):
    from app.desk_mcp import tools

    p = tools.patterns(rigged)
    roll = p["excursion_rollup"]
    assert roll["n_trades"] == 5 and roll["n_measured"] == 3 and roll["n_skipped"] == 2
    assert roll["summary"] == "3 measured / 5 trades"
    hp = roll["held_past_stop"]
    assert (hp["count"], hp["recovered"], hp["not_recovered"]) == (2, 1, 1)
    # ACME loser: cost is the ledger's own pnl + risk (negative = lost more than the stop)
    loser = tools.get_day(rigged, "2026-10-12")["positions"][0]
    expect = loser["pnl"] + loser["risk"]
    assert expect < 0
    assert hp["not_recovered_cost_usd"] == pytest.approx(expect, abs=0.01)
    assert hp["not_recovered_cost_r"] == pytest.approx(expect / loser["risk"], abs=1e-3)
    assert roll["exited_early"]["count"] == 1  # the 10-13 winner stays just under 1R
    assert roll["exited_early"]["winners_checked"] == 2
    assert roll["median_drawdown_minutes"] == 4  # median of 3, 5, 4
    assert p["tag_counts"]["held_past_stop"] == 2 and p["tag_counts"]["exited_early"] == 1
    blob = str(p).lower()
    assert "left_on" not in blob and "table" not in blob


def test_codes_reach_the_lesson_map(rigged):
    from app.desk import lessons as L
    from app.desk_mcp import tools

    d = tools.get_day(rigged, "2026-10-08")
    assert {"held_past_stop", "exited_early"} <= {x["pattern"] for x in d["lessons"]}
    assert L.is_known_pattern("held_past_stop") and L.is_known_pattern("exited_early")


def test_recurring_lessons_in_patterns(rigged):
    from app.desk_mcp import tools

    # held_past_stop on two different days => it is a recurring lesson
    rec = {x["pattern"]: x for x in tools.patterns(rigged)["lessons"]}
    assert rec["held_past_stop"]["days"] == 2 and rec["held_past_stop"]["positions"] == 2


def test_real_mode_uses_cache_only_and_degrades(tmp_path):
    from app.desk_mcp import tools

    p = tools.patterns(_ctx(tmp_path, "real"))  # nothing cached
    r = p["excursion_rollup"]
    assert r["bars_mode"] == "cache" and r["n_measured"] == 0 and r["n_skipped"] == 5
    assert "held_past_stop" not in p["tag_counts"]


def test_mock_mode_measures_with_mock_provider(tmp_path):
    from app.desk_mcp import tools

    ctx = _ctx(tmp_path, "mock")
    r = tools.patterns(ctx)["excursion_rollup"]
    assert r["measured"] and r["bars_mode"] == "mock" and r["n_trades"] == 5
    assert r["n_measured"] >= 1
    assert tools.patterns(ctx)["excursion_rollup"] == r  # deterministic
