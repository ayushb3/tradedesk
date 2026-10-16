"""Local-only parity: Python ledger vs the reference Fill Ledger, to the cent.

Skipped unless TRADEDESK_PARITY_DIR is set to a folder (outside the repo) holding the
day docs (``YYYY-MM-DD.json``) and ``expected.json``. Neither is ever committed.
TRADEDESK_PARITY_STOP (default 250) and TRADEDESK_PARITY_RISK_PCT (default 30, percent)
set the reference constants.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.desk.ledger import DayInput, Fill, Position, StopUsage, analyze_day
from app.desk.rules import AccountSize, LossLine, Rules

PARITY_DIR = os.environ.get("TRADEDESK_PARITY_DIR")
pytestmark = pytest.mark.skipif(not PARITY_DIR, reason="TRADEDESK_PARITY_DIR not set")

ET = ZoneInfo("America/New_York")


def _days() -> list[str]:
    if not PARITY_DIR:
        return []
    return sorted(p.stem for p in Path(PARITY_DIR).glob("2026-*.json") if p.stem != "expected")


def _rules() -> Rules:
    stop = float(os.environ.get("TRADEDESK_PARITY_STOP", "250"))
    pct = float(os.environ.get("TRADEDESK_PARITY_RISK_PCT", "30"))
    return Rules(
        daily_stop=-stop, daily_target=1, default_risk_pct=pct,
        loss_line=LossLine(amount=1, start_date=date(2026, 1, 1)),
        account_size=AccountSize(fallback=1),
    )


def _to_day(doc: dict) -> DayInput:
    ps = []
    for p in doc["positions"]:
        fills = [
            Fill(f["side"], f["qty"], f["px"], f["cash"],
                 datetime.fromisoformat(f"{f['d']}T{f['t']}").replace(tzinfo=ET), f.get("otype"))
            for f in p["fills"]
        ]
        ps.append(Position(fills=fills, status=p["status"], swing=bool(p.get("swing"))))
    stops = doc.get("stops") or {}
    ml = (doc.get("rules") or {}).get("max_loss_usd")
    return DayInput(
        date=date.fromisoformat(doc["date"]), positions=ps,
        stops=StopUsage(**{k: stops.get(k, 0) for k in ("placed", "filled", "moves", "pulled")}),
        max_loss=ml,
    )


def _eq(a, b):
    if b is None:
        return a is None
    return a is not None and abs(a - b) < 0.005


@pytest.mark.parametrize("day", _days())
def test_day_matches_reference(day):
    base = Path(PARITY_DIR)
    doc = json.loads((base / f"{day}.json").read_text())
    exp = json.loads((base / "expected.json").read_text())[day]
    res = analyze_day(_to_day(doc), _rules())
    s = res.stats
    assert _eq(s.net, exp["net"])
    assert s.entries == exp["entries"] and s.positions == exp["positions"]
    for mine, key in [(s.win_pct, "winPct"), (s.avg_win, "avgWin"), (s.avg_loss, "avgLoss"),
                      (s.max_risk, "maxRisk"), (s.deployed, "deployed")]:
        assert _eq(mine, exp[key]), key
    assert (s.rr is None) == (exp["rr"] is None) and (s.rr is None or abs(s.rr - exp["rr"]) < 1e-9)
    assert s.late_fills == exp["lateFills"]
    for mine, key in [(s.add_lower, "addLower"), (s.held_loser, "heldLoser"),
                      (s.gave_back, "gaveBack"), (s.runner_paid, "runnerPaid")]:
        assert mine == exp[key], key
    assert len(res.positions) == len(exp["pos"])
    for a, e in zip(res.positions, exp["pos"]):
        assert _eq(a.pnl, e["pnl"])
        assert [t.text for t in a.tags] == e["tags"]
        assert (a.r is None) == (e["r"] is None) and (a.r is None or abs(a.r - e["r"]) < 1e-9)
        sells = [r.pnl for r in a.fills if r.side == "S"]
        assert len(sells) == len(e["sellPnl"])
        assert all(_eq(m, x) for m, x in zip(sells, e["sellPnl"]))
