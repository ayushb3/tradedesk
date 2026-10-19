"""Enrichment durability across re-imports (issue #66, WS-0 of epic #64).

Re-importing/refreshing fills rebuilds every non-sim trade from scratch: the old
trade rows (and their `trade_enrichment` / `trade_tags`) are deleted and rebuilt.
User-entered enrichment — stop_price, notes/grade/checklist/screenshots, tags,
and the derived r_multiple — must survive that rebuild by being re-associated to
the rebuilt trade via a STABLE `trade_key` (the opening fill's provider_fill_id).

These tests set enrichment, run a re-import, and assert everything survives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest.imports import import_fills
from app.ingest.query import get_trade, list_trades, update_trade
from app.models.trades import Fill, Grade, Side, TradeUpdate

UTC = timezone.utc
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)


def _ts(minute_offset: int) -> datetime:
    return BASE + timedelta(minutes=minute_offset)


def _fill(i: int, symbol: str, side: Side, qty: float, price: float, minute: int) -> Fill:
    return Fill(
        provider_fill_id=f"f-{symbol}-{i:04d}",
        account="TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=_ts(minute),
        fees=0.0,
        simulated=False,
    )


class FakeProvider:
    """Returns a fixed fill set; records the `since` cursor for assertions."""

    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills
        self.calls: list[datetime | None] = []

    def get_fills(self, since=None) -> list[Fill]:
        self.calls.append(since)
        return list(self._fills)

    @property
    def last_since(self) -> datetime | None:
        return self.calls[-1]


def _only_trade(engine, symbol=None):
    trades = list_trades(simulated=False, symbol=symbol, engine=engine)
    assert len(trades) == 1
    return trades[0]


def _enrich(engine, trade_id: int, **kwargs):
    return update_trade(trade_id, TradeUpdate(**kwargs), engine=engine)


# --------------------------------------------------------------------------- #
# Full re-import (full=True): enrichment must survive the fills wipe + rebuild.
# --------------------------------------------------------------------------- #
def test_full_reimport_preserves_all_enrichment(temp_engine):
    # A closed AAA long: entry 10, exit 12.
    fills = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    ]
    import_fills(FakeProvider(fills), engine=temp_engine)

    trade = _only_trade(temp_engine, "AAA")
    # R is the one desk definition (no written max loss -> 30% of cost, "assumed"), not
    # derived from the stop: cost 1000, pnl 200 -> 200 / 300.
    _enrich(
        temp_engine,
        trade.id,
        notes="held to target, clean",
        grade=Grade.A,
        stop_price=8.0,
        checklist=[{"item": "at a level", "checked": True}],
        screenshots=["shot-1.png"],
    )

    before = get_trade(trade.id, engine=temp_engine)
    assert before.stop_price == 8.0
    assert before.r_multiple == pytest.approx(200 / 300) and before.r_basis == "assumed"

    # Full re-import: wipes fills, re-inserts the SAME provider_fill_ids, rebuilds.
    res = import_fills(FakeProvider(fills), engine=temp_engine, full=True)
    assert res["trades_built"] == 1

    after = _only_trade(temp_engine, "AAA")
    assert after.stop_price == 8.0
    assert after.r_multiple == pytest.approx(200 / 300)  # derived on read
    assert after.enrichment.notes == "held to target, clean"
    assert after.enrichment.grade == Grade.A
    assert after.enrichment.checklist == [{"item": "at a level", "checked": True}]
    assert after.enrichment.screenshots == ["shot-1.png"]


def test_full_reimport_preserves_tags(temp_engine):
    fills = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    ]
    import_fills(FakeProvider(fills), engine=temp_engine)

    # Create two tags directly, then attach them to the trade.
    from app.db.schema import tags as tags_t

    with temp_engine.begin() as conn:
        tag_a = conn.execute(tags_t.insert().values(name="A+ setup")).inserted_primary_key[0]
        tag_b = conn.execute(tags_t.insert().values(name="breakout")).inserted_primary_key[0]

    trade = _only_trade(temp_engine, "AAA")
    _enrich(temp_engine, trade.id, tag_ids=[int(tag_a), int(tag_b)])

    before = get_trade(trade.id, engine=temp_engine)
    assert sorted(t.name for t in before.tags) == ["A+ setup", "breakout"]

    import_fills(FakeProvider(fills), engine=temp_engine, full=True)

    after = _only_trade(temp_engine, "AAA")
    assert sorted(t.name for t in after.tags) == ["A+ setup", "breakout"]


# --------------------------------------------------------------------------- #
# Incremental sync: a refresh that appends one new fill must preserve enrichment
# on the untouched, already-closed trade.
# --------------------------------------------------------------------------- #
def test_incremental_sync_preserves_untouched_trade_enrichment(temp_engine):
    # First import: one closed AAA round-trip.
    first = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    ]
    import_fills(FakeProvider(first), engine=temp_engine)

    trade = _only_trade(temp_engine, "AAA")
    _enrich(
        temp_engine,
        trade.id,
        notes="first trade of the day",
        grade=Grade.B,
        stop_price=9.5,
    )
    before = get_trade(trade.id, engine=temp_engine)
    assert before.r_multiple is not None

    # Incremental refresh: provider returns the AAA overlap plus a NEW BBB fill.
    # This is a normal (non-full) refresh, so `since` must be set.
    second_provider = FakeProvider(
        [
            _fill(2, "AAA", Side.sell, 100, 12.0, 10),  # overlap, deduped
            _fill(3, "BBB", Side.buy, 50, 5.0, 20),  # new, opens a trade
        ]
    )
    import_fills(second_provider, engine=temp_engine)
    assert second_provider.last_since is not None  # incremental, not full

    # AAA (untouched) keeps every field; BBB is a fresh open trade.
    aaa = _only_trade(temp_engine, "AAA")
    assert aaa.stop_price == 9.5
    assert aaa.r_multiple == before.r_multiple
    assert aaa.enrichment.notes == "first trade of the day"
    assert aaa.enrichment.grade == Grade.B

    bbb = list_trades(simulated=False, symbol="BBB", engine=temp_engine)
    assert len(bbb) == 1
    assert bbb[0].enrichment.notes is None


# --------------------------------------------------------------------------- #
# The stop == exit -> exactly -1R artifact is preserved, not rejected (issue #66).
# --------------------------------------------------------------------------- #
def test_stop_equals_exit_is_preserved_as_minus_one_r(temp_engine):
    # Long stopped out at its stop: entry 10, exit 8, stop 8 -> -1R exactly.
    fills = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 8.0, 10),
    ]
    import_fills(FakeProvider(fills), engine=temp_engine)

    trade = _only_trade(temp_engine, "AAA")
    _enrich(temp_engine, trade.id, stop_price=8.0)

    before = get_trade(trade.id, engine=temp_engine)
    assert before.stop_price == 8.0
    assert before.r_multiple == pytest.approx(-200 / 300)  # R ignores the stop; stop kept as-is

    import_fills(FakeProvider(fills), engine=temp_engine, full=True)

    after = _only_trade(temp_engine, "AAA")
    assert after.stop_price == 8.0
    assert after.r_multiple == before.r_multiple
