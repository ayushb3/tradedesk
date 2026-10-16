"""Local-only: realized / fees / net per day against figures you wrote down from the broker.

Skipped in CI. Needs BOTH the real desk DB (``TRADEDESK_DB_PATH`` or ``<repo>/data/desk.db``) and
an expected file (``TRADEDESK_EXPECTED_REALIZED`` or the gitignored ``<repo>/data/expected-realized.json``)
shaped ``{"YYYY-MM-DD": {"realized": 0.0, "fees": 0.0, "net": 0.0}}``. Neither is ever committed.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from app.db.connection import get_engine
from app.desk import days as desk_days

REPO = Path(__file__).resolve().parents[2]
EXPECTED = Path(os.environ.get("TRADEDESK_EXPECTED_REALIZED") or REPO / "data" / "expected-realized.json")
DB = Path(os.environ.get("TRADEDESK_DB_PATH") or REPO / "data" / "desk.db")

pytestmark = pytest.mark.skipif(
    not (EXPECTED.exists() and DB.exists()), reason="no local expected-realized.json and desk DB"
)


def _cases():
    return sorted(json.loads(EXPECTED.read_text(encoding="utf-8")).items()) if EXPECTED.exists() else []


@pytest.mark.parametrize("day,want", _cases())
def test_day_matches_expected_to_the_cent(day, want):
    d = desk_days.get_day(date.fromisoformat(day), engine=get_engine(str(DB)))
    assert d is not None, f"no trades on {day}"
    s = d.result.stats
    assert round(s.realized_before_fees, 2) == pytest.approx(want["realized"], abs=0.005)
    assert round(s.fees, 2) == pytest.approx(want["fees"], abs=0.005)
    assert round(s.net, 2) == pytest.approx(want["net"], abs=0.005)
