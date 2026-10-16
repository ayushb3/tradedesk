"""day_fields store (issue #7). Fixtures are synthetic."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.connection import SCHEMA_VERSION
from app.desk.day_fields import get_day_fields, upsert_day_fields
from app.ingest.imports import import_fills
from app.models.trades import Fill, Side

D = "2026-10-14"


def test_schema_version_bumped():
    assert SCHEMA_VERSION == 8


def test_get_missing_returns_none(temp_engine):
    assert get_day_fields(D, temp_engine) is None


def test_unknown_field_rejected_and_nothing_written(temp_engine):
    with pytest.raises(ValueError, match="bogus"):
        upsert_day_fields(D, temp_engine, why="x", bogus=1)
    assert get_day_fields(D, temp_engine) is None
    with pytest.raises(ValueError, match="updated_at"):
        upsert_day_fields(D, temp_engine, updated_at=datetime.now())
    with pytest.raises(ValueError, match="date"):
        upsert_day_fields(D, temp_engine, date="2026-01-01")


def test_bad_types_rejected(temp_engine):
    with pytest.raises(ValueError):
        upsert_day_fields(D, temp_engine, callers="abc")
    with pytest.raises(ValueError):
        upsert_day_fields(D, temp_engine, stop_hit="yes")
    with pytest.raises(ValueError):
        upsert_day_fields(D, temp_engine, max_loss_declared="lots")


def test_create_defaults_and_roundtrip(temp_engine):
    r = upsert_day_fields(
        D, temp_engine, why="plan", callers=["a", "b"], size_written_first=True,
        max_loss_declared=50, stop_hit=False,
    )
    assert r.date == date(2026, 10, 14)
    assert r.callers == ["a", "b"]
    assert r.max_loss_declared == 50.0
    assert r.target_hit is None and r.notes == "" and r.how_felt == ""
    assert r.updated_at.tzinfo is not None
    assert r.model_dump(mode="json")["updated_at"].endswith("Z")
    assert get_day_fields(date(2026, 10, 14), temp_engine) == r


def test_merge_keeps_unspecified_and_none_clears(temp_engine):
    first = upsert_day_fields(D, temp_engine, why="plan", callers=["a"], stop_hit=True)
    second = upsert_day_fields(D, temp_engine, notes="later")
    assert second.why == "plan" and second.callers == ["a"] and second.stop_hit is True
    assert second.notes == "later"
    assert second.updated_at >= first.updated_at
    cleared = upsert_day_fields(D, temp_engine, stop_hit=None)
    assert cleared.stop_hit is None and cleared.why == "plan"


def test_rows_are_per_date(temp_engine):
    upsert_day_fields("2026-10-13", temp_engine, why="a")
    upsert_day_fields("2026-10-14", temp_engine, why="b")
    assert get_day_fields("2026-10-13", temp_engine).why == "a"


def test_survives_full_reimport(temp_engine):
    base = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)

    class Static:
        fills = [
            Fill(provider_fill_id="syn-1", account="TEST", symbol="AAA", side=Side.buy,
                 qty=10, price=1.0, ts=base, fees=0.0, simulated=False),
            Fill(provider_fill_id="syn-2", account="TEST", symbol="AAA", side=Side.sell,
                 qty=10, price=2.0, ts=base + timedelta(minutes=5), fees=0.0, simulated=False),
        ]

        def get_fills(self, since=None):
            return list(self.fills)

    import_fills(Static(), temp_engine)
    before = upsert_day_fields(D, temp_engine, why="kept", callers=["x"])
    import_fills(Static(), temp_engine, full=True)
    assert get_day_fields(D, temp_engine) == before
