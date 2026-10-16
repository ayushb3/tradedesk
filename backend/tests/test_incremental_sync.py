"""Incremental fills sync (`app.ingest.imports.import_fills`).

Verifies the `since` cursor behaviour: a first/empty-DB import (or an explicit
`full=True`) pulls the provider's full history and wipes existing non-sim
fills first; a normal refresh instead asks the provider only for fills newer
than the latest one already stored (with a 5-minute overlap guard) and merges
the result via the idempotent `_upsert_fills` dedupe on `provider_fill_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest.imports import import_fills
from app.ingest.query import list_trades
from app.models.trades import Fill, Side

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


class RecordingFakeProvider:
    """Records the `since` it was called with and returns a configurable set."""

    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills
        self.calls: list[datetime | None] = []

    def get_fills(self, since=None) -> list[Fill]:
        self.calls.append(since)
        return list(self._fills)

    @property
    def last_since(self) -> datetime | None:
        return self.calls[-1]


def _trades(engine, symbol=None):
    return list_trades(simulated=False, symbol=symbol, engine=engine)


def test_first_import_is_full(temp_engine):
    provider = RecordingFakeProvider(
        [
            _fill(1, "AAA", Side.buy, 100, 10.0, 0),
            _fill(2, "AAA", Side.sell, 100, 12.0, 10),
        ]
    )
    res = import_fills(provider, engine=temp_engine)

    assert provider.last_since is None
    assert res["fills_imported"] == 2
    assert res["trades_built"] == 1


def test_refresh_passes_since_cursor(temp_engine):
    t0 = _ts(10)
    first_provider = RecordingFakeProvider(
        [
            _fill(1, "AAA", Side.buy, 100, 10.0, 0),
            _fill(2, "AAA", Side.sell, 100, 12.0, 10),  # ts_utc == t0
        ]
    )
    import_fills(first_provider, engine=temp_engine)
    assert first_provider.last_since is None

    second_provider = RecordingFakeProvider([])
    import_fills(second_provider, engine=temp_engine)

    recorded_since = second_provider.last_since
    assert recorded_since is not None
    expected = t0 - timedelta(minutes=5)
    assert abs((recorded_since - expected).total_seconds()) < 5


def test_merge_is_additive(temp_engine):
    first_provider = RecordingFakeProvider(
        [
            _fill(1, "AAA", Side.buy, 100, 10.0, 0),
            _fill(2, "AAA", Side.sell, 100, 12.0, 10),
            _fill(3, "BBB", Side.buy, 50, 5.0, 20),
        ]
    )
    first = import_fills(first_provider, engine=temp_engine)
    assert first["fills_imported"] == 3

    # Second refresh: provider returns fills 3 (overlap), 4, 5.
    second_provider = RecordingFakeProvider(
        [
            _fill(3, "BBB", Side.buy, 50, 5.0, 20),
            _fill(4, "BBB", Side.sell, 50, 7.0, 30),
            _fill(5, "CCC", Side.buy, 10, 100.0, 40),
        ]
    )
    second = import_fills(second_provider, engine=temp_engine)

    assert second_provider.last_since is not None  # incremental, not full
    # id 3 already existed -> dedup'd; only 4 and 5 are newly inserted.
    assert second["fills_imported"] == 2

    with temp_engine.begin() as conn:
        from app.db.schema import fills as fills_t
        from sqlalchemy import select

        ids = sorted(
            r[0]
            for r in conn.execute(
                select(fills_t.c.provider_fill_id).where(fills_t.c.simulated.is_(False))
            ).fetchall()
        )
    assert ids == ["f-AAA-0001", "f-AAA-0002", "f-BBB-0003", "f-BBB-0004", "f-CCC-0005"]

    # Trades rebuilt over the union: AAA round-trip, BBB round-trip, CCC still open.
    after = _trades(temp_engine)
    assert len(after) == 3
    symbols = sorted(t.symbol for t in after)
    assert symbols == ["AAA", "BBB", "CCC"]


def test_full_flag_forces_wipe(temp_engine):
    first_provider = RecordingFakeProvider(
        [
            _fill(1, "AAA", Side.buy, 100, 10.0, 0),
            _fill(2, "AAA", Side.sell, 100, 12.0, 10),
        ]
    )
    import_fills(first_provider, engine=temp_engine)
    assert len(_trades(temp_engine)) == 1

    second_provider = RecordingFakeProvider(
        [
            _fill(3, "ZZZ", Side.buy, 50, 20.0, 0),
            _fill(4, "ZZZ", Side.sell, 50, 22.0, 5),
        ]
    )
    res = import_fills(second_provider, engine=temp_engine, full=True)

    assert second_provider.last_since is None
    assert res["fills_imported"] == 2

    after = _trades(temp_engine)
    assert len(after) == 1
    assert after[0].symbol == "ZZZ"


def _seed_fill(engine, provider_fill_id: str, symbol: str, side: Side,
               qty: float, price: float, minute: int) -> None:
    """Insert one non-sim fill directly, controlling its provider_fill_id."""
    from app.db.connection import init_db
    from app.db.schema import fills as fills_t

    init_db(engine)
    with engine.begin() as conn:
        conn.execute(
            fills_t.insert().values(
                provider_fill_id=provider_fill_id,
                account="MOCK-0001",
                symbol=symbol,
                side=side.value,
                qty=qty,
                price=price,
                ts_utc=_ts(minute).replace(tzinfo=None),  # stored naive-UTC
                fees=0.0,
                multiplier=1.0,
                simulated=False,
            )
        )


def test_mock_to_live_cutover_wipes_mock_fills(temp_engine):
    """A non-empty non-sim table full of mock-origin fills must force a full
    wipe on a NORMAL incremental import (no full=True) — the frontend auto-load
    hits this path first, and stale demo data must not survive."""
    # Seed a mock round-trip (provider_fill_id like "mock-%") — this is exactly
    # how mock-mode fills land in the table (simulated=False, mock- ids).
    _seed_fill(temp_engine, "mock-000001", "AAA", Side.buy, 100, 10.0, 0)
    _seed_fill(temp_engine, "mock-000002", "AAA", Side.sell, 100, 12.0, 10)
    assert len(_trades(temp_engine)) == 0  # trades not built for seeded fills yet

    # Normal incremental import (NO full param) with a real fill set.
    provider = RecordingFakeProvider(
        [
            _fill(1, "ZZZ", Side.buy, 50, 20.0, 0),
            _fill(2, "ZZZ", Side.sell, 50, 22.0, 5),
        ]
    )
    import_fills(provider, engine=temp_engine)

    # (a) cutover forced a full re-walk: provider was asked for the full history.
    assert provider.last_since is None

    # (b) no mock-origin fills remain.
    from app.db.schema import fills as fills_t
    from sqlalchemy import select

    with temp_engine.begin() as conn:
        mock_rows = conn.execute(
            select(fills_t.c.id).where(fills_t.c.provider_fill_id.like("mock-%"))
        ).fetchall()
    assert mock_rows == []

    after = _trades(temp_engine)
    assert len(after) == 1
    assert after[0].symbol == "ZZZ"
