"""Trade Review endpoint tests (M7a) — mock mode (conftest forces it).

Covers `GET /api/trades/{trade_id}/review`:
    - full payload shape (trade + fills + underlying + null option/premium)
    - fills ordered by seq
    - entry/exit role derivation for long AND short trades
    - a multi-fill scale-in/scale-out trade
    - 404 for a missing trade

Trades are built through the real FIFO importer into an isolated temp-file
engine, then read back via the query layer (same path the route uses). The
route handler itself is exercised through `get_trade`/`get_trade_fills`
directly with the temp engine so no real DB is touched; the HTTP wiring is a
thin pass-through verified by the router smoke.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest.imports import import_fills
from app.ingest.query import get_trade, get_trade_fills
from app.models.review import TradeReviewResponse, UnderlyingDescriptor
from app.models.trades import Direction, Fill, Side

UTC = timezone.utc
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # Wed, 09:30 ET


def _ts(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


class _FakeProvider:
    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills

    def get_fills(self, since=None) -> list[Fill]:
        return list(self._fills)


def _fill(i: int, symbol: str, side: Side, qty: float, price: float, minute: int,
          fees: float = 0.0) -> Fill:
    return Fill(
        provider_fill_id=f"f-{symbol}-{i:04d}",
        account="TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=_ts(minute),
        fees=fees,
        simulated=False,
    )


def _import(fills: list[Fill], engine) -> None:
    import_fills(_FakeProvider(fills), engine=engine)


def _build_review(trade_id: int, engine) -> TradeReviewResponse:
    """Assemble a TradeReviewResponse the same way the route handler does."""
    trade = get_trade(trade_id, engine=engine)
    assert trade is not None
    fills = get_trade_fills(trade_id, engine=engine)
    return TradeReviewResponse(
        trade=trade,
        fills=fills,
        underlying=UnderlyingDescriptor(
            symbol=trade.symbol,
            session_date=trade.session_date.isoformat(),
        ),
        option=None,
        premium_bars=None,
    )


def _only_trade_id(engine) -> int:
    from app.ingest.query import list_trades

    trades = list_trades(engine=engine)
    assert len(trades) == 1
    return trades[0].id


# --------------------------------------------------------------------------- #
# Payload shape
# --------------------------------------------------------------------------- #
def test_review_payload_shape(temp_engine):
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0, fees=1.0),
            _fill(2, "RIVN", Side.sell, 100, 13.72, 38, fees=0.0),
        ],
        temp_engine,
    )
    review = _build_review(_only_trade_id(temp_engine), temp_engine)

    assert review.trade.symbol == "RIVN"
    assert review.underlying.symbol == "RIVN"
    assert review.underlying.session_date == "2026-06-10"
    assert review.underlying.timeframes == ["1m", "5m"]
    # Option/premium blocks are stubbed null in M7a.
    assert review.option is None
    assert review.premium_bars is None
    assert len(review.fills) == 2


# --------------------------------------------------------------------------- #
# Fills ordering + role derivation
# --------------------------------------------------------------------------- #
def test_fills_ordered_by_seq(temp_engine):
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0),
            _fill(2, "RIVN", Side.buy, 100, 13.42, 4),
            _fill(3, "RIVN", Side.sell, 200, 13.72, 38),
        ],
        temp_engine,
    )
    review = _build_review(_only_trade_id(temp_engine), temp_engine)
    seqs = [f.seq for f in review.fills]
    assert seqs == sorted(seqs)
    assert seqs == [0, 1, 2]


def test_role_derivation_long(temp_engine):
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0),
            _fill(2, "RIVN", Side.sell, 100, 13.72, 38),
        ],
        temp_engine,
    )
    review = _build_review(_only_trade_id(temp_engine), temp_engine)
    assert review.trade.direction == Direction.long
    by_side = {f.side: f.role for f in review.fills}
    assert by_side[Side.buy] == "entry"
    assert by_side[Side.sell] == "exit"


def test_role_derivation_short(temp_engine):
    _import(
        [
            _fill(1, "HOOD", Side.sell, 100, 22.0, 0),  # sell-to-open
            _fill(2, "HOOD", Side.buy, 100, 20.0, 30),  # buy-to-close
        ],
        temp_engine,
    )
    review = _build_review(_only_trade_id(temp_engine), temp_engine)
    assert review.trade.direction == Direction.short
    by_side = {f.side: f.role for f in review.fills}
    assert by_side[Side.sell] == "entry"
    assert by_side[Side.buy] == "exit"


# --------------------------------------------------------------------------- #
# Multi-fill scale-in / scale-out
# --------------------------------------------------------------------------- #
def test_multi_fill_scale_in_out(temp_engine):
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0),
            _fill(2, "RIVN", Side.buy, 50, 13.42, 5),
            _fill(3, "RIVN", Side.sell, 50, 13.60, 30),
            _fill(4, "RIVN", Side.sell, 100, 13.72, 38),
        ],
        temp_engine,
    )
    review = _build_review(_only_trade_id(temp_engine), temp_engine)
    assert len(review.fills) == 4
    assert [f.seq for f in review.fills] == [0, 1, 2, 3]
    roles = [f.role for f in review.fills]
    # buys are entries, sells are exits (long trade), in seq order.
    assert roles == ["entry", "entry", "exit", "exit"]


# --------------------------------------------------------------------------- #
# 404
# --------------------------------------------------------------------------- #
def test_review_missing_trade_returns_none(temp_engine):
    assert get_trade(999999, engine=temp_engine) is None
    assert get_trade_fills(999999, engine=temp_engine) == []


def test_review_404_via_http(isolated_db):
    """The HTTP handler raises 404 for an unknown trade id.

    The review router is mounted on a local app here so this test does not
    depend on the PM's `main.py` registration (glue commit G1).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes.review import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    resp = client.get("/api/trades/999999/review")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "trade not found"}
