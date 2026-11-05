"""Post-close fill <-> callout report. Invented callouts and synthetic fills only."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.desk import callout_report as R
from app.learn.callouts import Event
from tests.test_desk_api import client, rules_file  # noqa: F401  (fixtures)

UTC = timezone.utc
DAY = date(2026, 10, 13)
T0 = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)  # 06:45 PT
m = timedelta


def _fill(side, at, *, ticker="SPY", typ="C", tid=1):
    return R.FillIn(tid, None, ticker, typ, side, 1, 1.0, at)


def _ev(kind, at, *, ticker="SPY", called=None, side="long"):
    return Event(kind, ticker, called or ticker, side if kind == "entry" else None, at)


def test_buy_matches_nearest_entry_same_group_and_direction():
    evs = [_ev("entry", T0 - m(minutes=3), called="SPX"),
           _ev("entry", T0 - m(minutes=1), side="short"),        # wrong direction
           _ev("entry", T0 - m(seconds=30), ticker="QQQ")]       # wrong ticker
    r = R.build(DAY, [_fill("B", T0)], evs)
    row = r["fills"][0]
    assert row["callout"]["ticker"] == "SPX" and row["gap_s"] == 180
    assert r["matched"] == 1 and r["unmatched"] == 0
    assert [c["ticker"] for c in r["calls_not_taken"]] == ["SPY", "QQQ"]


def test_put_buy_wants_a_put_call():
    evs = [_ev("entry", T0, side="long"), _ev("entry", T0 + m(minutes=2), side="short")]
    row = R.build(DAY, [_fill("B", T0, typ="P")], evs)["fills"][0]
    assert row["callout"]["side"] == "short" and row["gap_s"] == -120


def test_sell_matches_trim_or_exit_and_outside_window_is_unmatched():
    evs = [_ev("entry", T0), _ev("trim", T0 + m(minutes=6)), _ev("exit", T0 + m(minutes=40))]
    fills = [_fill("B", T0 + m(seconds=20)), _fill("S", T0 + m(minutes=5)),
             _fill("S", T0 + m(minutes=25))]
    r = R.build(DAY, fills, evs)
    assert [f["callout"] and f["callout"]["type"] for f in r["fills"]] == ["entry", "trim", None]
    assert (r["matched"], r["unmatched"]) == (2, 1)
    assert r["calls_not_taken"] == []


def test_no_callouts_file_is_reported_not_guessed():
    r = R.build(DAY, [_fill("B", T0)], None)
    assert r["callouts_found"] is False and r["fills"][0]["callout"] is None


def test_endpoint_reads_the_listener_dir(client, tmp_path, monkeypatch):  # noqa: F811
    root = tmp_path / "calls"
    (root / "callouts").mkdir(parents=True)
    rows = [{"type": "entry", "ticker": "SPX", "direction": "call", "confidence": 0.95,
             "wall": (T0 - m(minutes=2)).isoformat(), "flags": []},
            {"type": "entry", "ticker": "NVDA", "direction": "put", "confidence": 0.95,
             "wall": (T0 + m(minutes=50)).isoformat(), "flags": []}]
    (root / "callouts" / f"callouts-{DAY}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setenv("TRADEDESK_CALLOUTS_DIR", str(root))
    d = client.get(f"/api/days/{DAY}/callouts").json()
    assert d["callouts_found"] is True and d["n_callouts"] == 2
    first = d["fills"][0]
    assert first["side"] == "B" and first["callout"]["ticker"] == "SPX"
    assert first["gap_s"] == 120 and first["ts_pt"] == "06:45:00 PT"
    assert [c["ticker"] for c in d["calls_not_taken"]] == ["NVDA"]


def test_endpoint_without_listener_data(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("TRADEDESK_CALLOUTS_DIR", str(tmp_path / "missing"))
    d = client.get(f"/api/days/{DAY}/callouts").json()
    assert d["callouts_found"] is False and d["unmatched"] == len(d["fills"]) > 0
