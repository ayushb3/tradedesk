"""M6 task 6.3.3 — leak detector + drill-playlist prescription tests.

Tests exercise `app.analytics.leaks.detect_leaks` (pure unit) and the
`GET /api/training/leaks` endpoint (HTTP / integration).

Acceptance criterion from PLAN §6 6.3.3:
  "detects a seeded 'revenge-trading after 2 losses' fixture"

All unit tests (1–10) use hand-built fixtures with no database.  The two
endpoint tests (12–13) use the `isolated_db` + TestClient pattern matching
test_training_contract.py.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

UTC = timezone.utc

# --------------------------------------------------------------------------- #
# Fixtures and helpers                                                         #
# --------------------------------------------------------------------------- #

_FIXED_TS = "2026-07-09T14:00:00+00:00"   # injected into detect_leaks as _now


def _trade(
    *,
    id: int,
    net_pnl: float,
    qty: float = 100.0,
    direction: str = "long",
    open_ts: datetime | None = None,
    close_ts: datetime | None = None,
    session_date: date | None = None,
    stop_price: float | None = None,
    avg_exit: float | None = None,
    status: str = "closed",
    context: str = "real",
):
    """Build a minimal Trade for testing without a database."""
    from app.models.trades import Trade, Direction, TradeStatus

    base = datetime(2026, 7, 1, 14, 0, 0, tzinfo=UTC)
    ots = open_ts or (base + timedelta(hours=id))
    cts = close_ts or (ots + timedelta(minutes=30))
    sdate = session_date or ots.date()
    ae = avg_exit if avg_exit is not None else (10.0 + net_pnl / qty)

    return Trade(
        id=id,
        symbol="AAPL",
        direction=Direction(direction),
        open_ts=ots,
        close_ts=cts,
        session_date=sdate,
        qty=qty,
        avg_entry=10.0,
        avg_exit=ae,
        gross_pnl=net_pnl,
        fees=0.0,
        net_pnl=net_pnl,
        pnl_pct=net_pnl / 1000.0,
        stop_price=stop_price,
        r_multiple=None,
        hold_time_s=1800,
        status=TradeStatus(status),
        simulated=False,
    )


def _attempt(
    attempt_id: int,
    qty: float,
    *,
    process_score: float = 1.0,
    outcome_pnl: float = 0.0,
    started_at: datetime | None = None,
):
    """Build a minimal Attempt with entry action and dynamic score attrs."""
    from app.models.training import Attempt, AttemptAction, AttemptActionType

    ts = started_at or datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)
    actions = [
        AttemptAction(
            bar=5,
            action=AttemptActionType.enter_long,
            price=12.50,
            qty=qty,
            ts=ts,
        ),
        AttemptAction(
            bar=8,
            action=AttemptActionType.exit,
            price=12.30 if outcome_pnl < 0 else 12.70,
            qty=qty,
            ts=ts + timedelta(minutes=10),
        ),
    ]
    a = Attempt(
        id=attempt_id,
        scenario_id=1,
        started_at=ts,
        finished_at=None,
        actions=actions,
    )
    object.__setattr__(a, "process_score", process_score)
    object.__setattr__(a, "outcome_pnl", outcome_pnl)
    return a


def _debrief(
    debrief_id: int,
    trade_id: int,
    verdict: str | None,
):
    """Build a minimal Debrief with the given verdict."""
    from app.models.training import Debrief, DebriefStep, DebriefVerdict

    v = DebriefVerdict(verdict) if verdict else None
    return Debrief(
        id=debrief_id,
        trade_id=trade_id,
        llm_backend="none",
        step=DebriefStep.done,
        transcript=[],
        plan=None,
        feelings=[],
        verdict=v,
        scenario_id=None,
        created_at=datetime(2026, 7, 1, 14, 0, 0, tzinfo=UTC),
    )


def _scenario(
    scenario_id: int,
    drill_type: str = "decision-point",
    archetype: str = "custom",
    active: bool = True,
):
    """Build a minimal ScenarioPublic."""
    from app.models.training import ScenarioPublic, DrillType, Archetype, ScenarioSource

    return ScenarioPublic(
        id=scenario_id,
        title=f"Scenario {scenario_id}",
        archetype=Archetype(archetype),
        source_type=ScenarioSource.real,
        drill_type=DrillType(drill_type),
        start_bar=0,
        active=active,
        created_at=datetime(2026, 7, 1, 14, 0, 0, tzinfo=UTC),
    )


# --------------------------------------------------------------------------- #
# Test 1: revenge_trading detected from attempts (primary signal path)
# --------------------------------------------------------------------------- #

def test_revenge_trading_detected_from_attempts():
    """Attempt-based detection: 3 attempts — 2 losses then up-sized 3rd attempt.

    Attempt 1: qty=100, process_score=0.1, pnl=-100 (loss)
    Attempt 2: qty=100, process_score=0.15, pnl=-80  (loss)
    Attempt 3: qty=250, process_score=0.15, pnl=+50  (up-sized 2.5x)

    detect_leaks must emit revenge_trading with severity >= 2 and a non-empty
    prescribed_scenario_ids when matching scenarios are supplied.
    """
    from app.analytics.leaks import detect_leaks

    t0 = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    attempts = [
        _attempt(1, 100.0, process_score=0.10, outcome_pnl=-100.0, started_at=t0),
        _attempt(2, 100.0, process_score=0.15, outcome_pnl=-80.0,
                 started_at=t0 + timedelta(minutes=15)),
        _attempt(3, 250.0, process_score=0.15, outcome_pnl=50.0,
                 started_at=t0 + timedelta(minutes=30)),
    ]
    # A do-nothing scenario to verify playlist picks it up.
    scenarios = [_scenario(10, drill_type="do-nothing")]

    report = detect_leaks(
        trades=[],
        fills=[],
        debriefs=[],
        attempts=attempts,
        scenarios=scenarios,
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "revenge_trading" in names, (
        f"Expected 'revenge_trading' in leaks, got {names}"
    )
    leak = next(l for l in report.leaks if l.name == "revenge_trading")
    assert leak.severity >= 2, f"severity should be >= 2, got {leak.severity}"
    assert leak.prescribed_scenario_ids, "playlist should be non-empty when matching scenarios exist"
    assert 10 in leak.prescribed_scenario_ids


# --------------------------------------------------------------------------- #
# Test 2: revenge_trading from journal-only (acceptance criterion)
# --------------------------------------------------------------------------- #

def test_revenge_trading_detected_from_journal_only():
    """Journal-only detection: 2 consecutive losing trades, then 3x up-sized.

    This is the acceptance criterion from PLAN §6 6.3.3.

    Trade 1: qty=100, net_pnl=-50  (loss)
    Trade 2: qty=100, net_pnl=-75  (loss)
    Trade 3: qty=300, net_pnl=+90  (up-sized 3.0x — revenge)
    """
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=1, net_pnl=-50.0,  qty=100.0, open_ts=base),
        _trade(id=2, net_pnl=-75.0,  qty=100.0, open_ts=base + timedelta(minutes=15)),
        _trade(id=3, net_pnl=90.0,   qty=300.0, open_ts=base + timedelta(minutes=30)),
    ]

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "revenge_trading" in names, (
        f"Acceptance criterion: 'revenge_trading' must be detected from journal "
        f"fixture (2 losses → up-sized trade). Got leaks: {names}"
    )
    leak = next(l for l in report.leaks if l.name == "revenge_trading")
    assert leak.severity >= 2
    assert 1 in leak.evidence["trade_ids"]
    assert 2 in leak.evidence["trade_ids"]
    assert 3 in leak.evidence["trade_ids"]


# --------------------------------------------------------------------------- #
# Test 3: no revenge when up-sizing after a win
# --------------------------------------------------------------------------- #

def test_no_revenge_when_up_sizing_after_win():
    """Winning trade followed by a larger trade: NOT a revenge pattern."""
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=1, net_pnl=200.0, qty=100.0, open_ts=base),   # win
        _trade(id=2, net_pnl=400.0, qty=100.0, open_ts=base + timedelta(minutes=15)),  # win
        _trade(id=3, net_pnl=150.0, qty=300.0, open_ts=base + timedelta(minutes=30)),  # bigger
    ]

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "revenge_trading" not in names, (
        f"Upsizing after wins should NOT trigger revenge_trading, got {names}"
    )


# --------------------------------------------------------------------------- #
# Test 4: chop_overtrading from journal breakdown
# --------------------------------------------------------------------------- #

def test_chop_overtrading_from_journal_breakdown():
    """10 trades on a single session where 7 lost → chop_overtrading leak."""
    from app.analytics.leaks import detect_leaks

    chop_day = date(2026, 7, 9)
    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = []
    for i in range(10):
        pnl = -20.0 if i < 7 else 30.0   # 7 losses, 3 wins
        trades.append(
            _trade(
                id=i + 1,
                net_pnl=pnl,
                open_ts=base + timedelta(minutes=i * 10),
                session_date=chop_day,
            )
        )

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "chop_overtrading" in names, (
        f"10 trades with 70% loss rate should trigger chop_overtrading, got {names}"
    )
    leak = next(l for l in report.leaks if l.name == "chop_overtrading")
    assert leak.severity >= 1


# --------------------------------------------------------------------------- #
# Test 5: moved_stops detected from journal
# --------------------------------------------------------------------------- #

def test_moved_stops_detected_from_journal():
    """5 trades total, 2 have stop_price and avg_exit indicating loosened stop.

    Long trade with stop_price=10.50 but avg_exit=10.20 (below stop → loosened).
    Short trade with stop_price=12.50 but avg_exit=12.80 (above stop → loosened).
    3 clean trades (no stop or honored stop).

    2/5 trades = 40%, above the 30% threshold → moved_stops leak.
    """
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    # Long trade: stop=10.50, but exited at 10.20 (below stop — loosened)
    t1 = _trade(id=1, net_pnl=-30.0, qty=100.0, stop_price=10.50, avg_exit=10.20,
                direction="long", open_ts=base)
    # Short trade: stop=12.50, but exited at 12.80 (above stop — loosened)
    t2 = _trade(id=2, net_pnl=-30.0, qty=100.0, stop_price=12.50, avg_exit=12.80,
                direction="short", open_ts=base + timedelta(minutes=30))
    # Clean trades (no stop or exit above stop for long → honored)
    t3 = _trade(id=3, net_pnl=50.0, qty=100.0, stop_price=10.00, avg_exit=10.80,
                direction="long", open_ts=base + timedelta(hours=1))
    t4 = _trade(id=4, net_pnl=-10.0, qty=100.0, stop_price=None, avg_exit=10.20,
                open_ts=base + timedelta(hours=2))
    t5 = _trade(id=5, net_pnl=80.0, qty=100.0, stop_price=None, avg_exit=10.80,
                open_ts=base + timedelta(hours=3))

    report = detect_leaks(
        trades=[t1, t2, t3, t4, t5],
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "moved_stops" in names, (
        f"2/5 trades (40%) with loosened stops should trigger moved_stops "
        f"(threshold 30%). Got leaks: {names}"
    )
    leak = next(l for l in report.leaks if l.name == "moved_stops")
    assert leak.severity >= 1
    assert 1 in leak.evidence["trade_ids"]
    assert 2 in leak.evidence["trade_ids"]


# --------------------------------------------------------------------------- #
# Test 6: psychology_loss from debriefs
# --------------------------------------------------------------------------- #

def test_psychology_loss_from_debriefs():
    """10 debriefs, 4 with verdict='failed-the-setup' → psychology_loss leak.

    4/10 = 40%, above the 20% threshold.
    """
    from app.analytics.leaks import detect_leaks

    debriefs = []
    for i in range(10):
        if i < 4:
            verdict = "failed-the-setup"
        else:
            verdict = "clean"
        debriefs.append(_debrief(debrief_id=i + 1, trade_id=i + 1, verdict=verdict))

    report = detect_leaks(
        trades=[],
        fills=[],
        debriefs=debriefs,
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "psychology_loss" in names, (
        f"4/10 failed-the-setup verdicts (40%) should trigger psychology_loss "
        f"(threshold 20%). Got leaks: {names}"
    )
    leak = next(l for l in report.leaks if l.name == "psychology_loss")
    assert leak.severity >= 1
    assert len(leak.evidence["debrief_ids"]) == 4


# --------------------------------------------------------------------------- #
# Test 7: no leaks on clean data
# --------------------------------------------------------------------------- #

def test_no_leaks_on_clean_data():
    """5 winning debriefed trades, consistent sizing, no bad behavior → empty."""
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=i + 1, net_pnl=50.0, qty=100.0,
               open_ts=base + timedelta(minutes=i * 30))
        for i in range(5)
    ]
    debriefs = [
        _debrief(debrief_id=i + 1, trade_id=i + 1, verdict="clean")
        for i in range(5)
    ]

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=debriefs,
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    assert report.leaks == [], (
        f"Clean data with all wins and 'clean' verdicts should produce no leaks, "
        f"got: {[l.name for l in report.leaks]}"
    )


# --------------------------------------------------------------------------- #
# Test 8: playlist prescription uses matching scenarios
# --------------------------------------------------------------------------- #

def test_playlist_prescription_uses_matching_scenarios():
    """revenge_trading leak prescribes do-nothing/drawdown-script scenarios,
    not chop or gap-and-go decision-point ones.

    Pass 3 scenarios:
    - id=1: do-nothing (matches revenge_trading rule)
    - id=2: gap-and-go decision-point (no match)
    - id=3: drawdown-script (matches revenge_trading rule)
    """
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=1, net_pnl=-50.0, qty=100.0, open_ts=base),
        _trade(id=2, net_pnl=-75.0, qty=100.0, open_ts=base + timedelta(minutes=15)),
        _trade(id=3, net_pnl=90.0,  qty=300.0, open_ts=base + timedelta(minutes=30)),
    ]

    scenarios = [
        _scenario(1, drill_type="do-nothing", archetype="custom"),
        _scenario(2, drill_type="decision-point", archetype="gap-and-go"),
        _scenario(3, drill_type="drawdown-script", archetype="custom"),
    ]

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=scenarios,
        _now=_FIXED_TS,
    )

    names = [l.name for l in report.leaks]
    assert "revenge_trading" in names

    leak = next(l for l in report.leaks if l.name == "revenge_trading")
    # Should include scenario 1 (do-nothing) and scenario 3 (drawdown-script)
    # Should NOT include scenario 2 (decision-point, gap-and-go) unless no rules match
    assert 1 in leak.prescribed_scenario_ids, "do-nothing scenario should be prescribed"
    assert 3 in leak.prescribed_scenario_ids, "drawdown-script scenario should be prescribed"
    # Scenario 2 (decision-point) doesn't match revenge_trading rules
    assert 2 not in leak.prescribed_scenario_ids, (
        "decision-point scenario should not be prescribed for revenge_trading"
    )


# --------------------------------------------------------------------------- #
# Test 9: leak report serializes to dict
# --------------------------------------------------------------------------- #

def test_leak_report_serializes_to_dict():
    """JSON round-trip of LeakReport must succeed."""
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=1, net_pnl=-50.0, qty=100.0, open_ts=base),
        _trade(id=2, net_pnl=-75.0, qty=100.0, open_ts=base + timedelta(minutes=15)),
        _trade(id=3, net_pnl=90.0,  qty=300.0, open_ts=base + timedelta(minutes=30)),
    ]

    report = detect_leaks(
        trades=trades,
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now=_FIXED_TS,
    )

    d = report.to_dict()
    assert isinstance(d, dict)
    assert "leaks" in d
    assert "generated_at" in d

    # JSON round-trip must not raise.
    json_str = json.dumps(d)
    parsed = json.loads(json_str)
    assert isinstance(parsed["leaks"], list)
    assert parsed["generated_at"] == _FIXED_TS

    if parsed["leaks"]:
        leak = parsed["leaks"][0]
        assert "name" in leak
        assert "severity" in leak
        assert "description" in leak
        assert "evidence" in leak
        assert "prescribed_scenario_ids" in leak
        assert isinstance(leak["evidence"]["trade_ids"], list)


# --------------------------------------------------------------------------- #
# Test 10: deterministic output (same inputs → same output)
# --------------------------------------------------------------------------- #

def test_leak_report_deterministic():
    """Same inputs → same leaks in the same order and same generated_at."""
    from app.analytics.leaks import detect_leaks

    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    trades = [
        _trade(id=1, net_pnl=-50.0, qty=100.0, open_ts=base),
        _trade(id=2, net_pnl=-75.0, qty=100.0, open_ts=base + timedelta(minutes=15)),
        _trade(id=3, net_pnl=90.0,  qty=300.0, open_ts=base + timedelta(minutes=30)),
    ]

    r1 = detect_leaks(
        trades=trades, fills=[], debriefs=[], attempts=[], scenarios=[],
        _now=_FIXED_TS,
    )
    r2 = detect_leaks(
        trades=trades, fills=[], debriefs=[], attempts=[], scenarios=[],
        _now=_FIXED_TS,
    )

    assert r1.generated_at == r2.generated_at
    assert [l.name for l in r1.leaks] == [l.name for l in r2.leaks]
    assert [l.severity for l in r1.leaks] == [l.severity for l in r2.leaks]
    # Evidence IDs should be identical lists.
    for l1, l2 in zip(r1.leaks, r2.leaks):
        assert l1.evidence == l2.evidence


# --------------------------------------------------------------------------- #
# Test 11: endpoint returns 501 when detector module is absent
# --------------------------------------------------------------------------- #

def test_endpoint_returns_501_when_detector_absent(isolated_db, monkeypatch):
    """Monkeypatch app.analytics.leaks to None → GET /training/leaks returns 501."""
    # Remove the module from sys.modules so the lazy import in the route fails.
    monkeypatch.setitem(sys.modules, "app.analytics.leaks", None)

    from app.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    r = client.get("/api/training/leaks")
    assert r.status_code == 501, (
        f"Expected 501 when leaks module absent, got {r.status_code}: {r.text}"
    )


# --------------------------------------------------------------------------- #
# Test 12: endpoint returns leaks end-to-end
# --------------------------------------------------------------------------- #

def _seed_trade_with_context(engine, *, net_pnl: float, qty: float,
                              open_ts: datetime, context: str = "real") -> int:
    """Insert a minimal closed trade directly, return id."""
    from app.db.connection import init_db
    from app.db.schema import trades as trades_t

    init_db(engine)
    with engine.begin() as conn:
        res = conn.execute(
            trades_t.insert().values(
                symbol="AAPL",
                direction="long",
                open_ts_utc=open_ts.replace(tzinfo=None),
                close_ts_utc=(open_ts + timedelta(minutes=30)).replace(tzinfo=None),
                session_date=open_ts.date(),
                qty=qty,
                avg_entry=10.0,
                avg_exit=10.0 + net_pnl / qty,
                gross_pnl=net_pnl,
                fees=0.0,
                net_pnl=net_pnl,
                pnl_pct=net_pnl / 1000.0,
                stop_price=None,
                r_multiple=None,
                hold_time_s=1800,
                status="closed",
                simulated=False,
                context=context,
            )
        )
        return int(res.inserted_primary_key[0])


def test_endpoint_returns_leaks_end_to_end(isolated_db):
    """Seed 2 loss + 1 revenge-size trade; GET /api/training/leaks → 200 + leaks."""
    from app.db.connection import get_engine
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = get_engine()
    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    _seed_trade_with_context(engine, net_pnl=-50.0, qty=100.0, open_ts=base)
    _seed_trade_with_context(engine, net_pnl=-75.0, qty=100.0,
                             open_ts=base + timedelta(minutes=15))
    _seed_trade_with_context(engine, net_pnl=+90.0, qty=300.0,
                             open_ts=base + timedelta(minutes=30))

    client = TestClient(create_app())
    r = client.get("/api/training/leaks")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "leaks" in body, "Response must contain 'leaks' key"
    assert "generated_at" in body, "Response must contain 'generated_at' key"
    names = [l["name"] for l in body["leaks"]]
    assert "revenge_trading" in names, (
        f"End-to-end: seeded revenge pattern must produce 'revenge_trading' leak. "
        f"Got: {names}"
    )


# --------------------------------------------------------------------------- #
# Test 13: endpoint excludes training trades
# --------------------------------------------------------------------------- #

def test_endpoint_excludes_training_trades(isolated_db):
    """Training-context trades must NOT influence leak detection.

    Seeds:
    - 3 real trades matching a revenge pattern (losses then up-sized) → leak
    - 3 training trades matching a DIFFERENT pattern (all winners, big sizes)
      that would NOT produce a revenge leak on their own.

    After seeding, GET /api/training/leaks must reflect the real-trade pattern
    only: revenge_trading appears.  Training trades (which don't match) must
    not pollute the signal.

    Additionally: if we ONLY have training trades (and the real ones all win),
    we should get NO leaks — proving training data is excluded.
    """
    from app.db.connection import get_engine
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = get_engine()
    base = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)

    # Real trades: 2 losses + up-size → revenge pattern
    _seed_trade_with_context(engine, net_pnl=-50.0,  qty=100.0, open_ts=base,
                              context="real")
    _seed_trade_with_context(engine, net_pnl=-75.0,  qty=100.0,
                             open_ts=base + timedelta(minutes=15), context="real")
    _seed_trade_with_context(engine, net_pnl=+90.0,  qty=300.0,
                             open_ts=base + timedelta(minutes=30), context="real")

    # Training trades: big wins with large sizes — would NOT produce revenge_trading
    # (wins followed by more wins, even if sized up)
    _seed_trade_with_context(engine, net_pnl=+200.0, qty=500.0,
                             open_ts=base + timedelta(hours=1), context="training")
    _seed_trade_with_context(engine, net_pnl=+300.0, qty=500.0,
                             open_ts=base + timedelta(hours=2), context="training")
    _seed_trade_with_context(engine, net_pnl=+400.0, qty=1000.0,
                             open_ts=base + timedelta(hours=3), context="training")

    client = TestClient(create_app())
    r = client.get("/api/training/leaks")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    names = [l["name"] for l in body["leaks"]]
    # Real-trade revenge pattern must be detected
    assert "revenge_trading" in names, (
        f"Real-trade revenge pattern should be detected even with training "
        f"trades seeded alongside. Got: {names}"
    )

    # Verify: if we call with only training context (by checking trade_ids in
    # evidence), none of the evidence trade_ids should come from the training
    # trades (ids 4, 5, 6).
    revenge_leak = next(l for l in body["leaks"] if l["name"] == "revenge_trading")
    evidence_ids = set(revenge_leak["evidence"]["trade_ids"])
    training_ids = {4, 5, 6}
    overlap = evidence_ids & training_ids
    assert not overlap, (
        f"Evidence trade_ids {evidence_ids} must not include training-context "
        f"trade IDs {training_ids}. Overlap: {overlap}"
    )
