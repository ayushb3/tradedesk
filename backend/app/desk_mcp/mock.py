"""Mock mode: a fixture DB built from a small SYNTHETIC set of option orders.

The orders are made up (fictional tickers, round numbers). They go through the
normal import path (``RobinhoodExport`` -> ``import_fills``), so the fixture DB
exercises the same code as a real one. The file lives under the OS temp dir and is
rebuilt when missing. No real data is ever read, copied or written here.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.connection import get_engine, init_db
from app.desk.plans import add_plan
from app.desk.rules import EXAMPLE_PATH, Rules, load_rules
from app.ingest.imports import import_fills
from app.ingest.robinhood import RobinhoodExport

UTC = timezone.utc
FIXTURE_VERSION = 1
MOCK_DB_NAME = f"tradedesk-mock-desk-v{FIXTURE_VERSION}.db"


def mock_db_path() -> Path:
    return Path(tempfile.gettempdir()) / MOCK_DB_NAME


def mock_rules() -> Rules:
    """Placeholder rules; the account-size file is pointed at nothing so only the
    placeholder fallback is ever used."""
    rules = load_rules(EXAMPLE_PATH)
    rules.account_size.source_path = "mock/does-not-exist.json"
    return rules


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _order(oid, side, at, px, qty, *, net, direction, sym, strike, expiry, trigger="immediate"):
    leg = {
        "side": side, "position_effect": "open" if side == "buy" else "close",
        "option_type": "call", "strike_price": f"{strike:.4f}", "expiration_date": expiry,
        "executions": [{"id": f"{oid}-x", "price": f"{px:.8f}", "quantity": f"{qty:.5f}",
                        "timestamp": _iso(at)}],
    }
    return {
        "id": oid, "chain_symbol": sym, "state": "filled", "trigger": trigger, "type": "limit",
        "stop_price": "1.0000" if trigger == "stop" else None,
        "processed_quantity": f"{qty:.5f}", "net_amount": f"{net:.2f}",
        "net_amount_direction": direction, "trade_value_multiplier": "100.0000",
        "created_at": _iso(at), "updated_at": _iso(at), "legs": [leg],
    }


def synthetic_orders() -> list[dict]:
    """Four made-up days: a stopped winner, a late exit, a big loser, a clean winner."""
    m = timedelta
    out: list[dict] = []

    def trip(tag, day, sym, strike, buy_px, sell_px, qty, *, open_min=15, hold_min=10,
             sell_trigger="immediate", fee=1.0):
        t0 = datetime(2026, 10, day, 13, 30, tzinfo=UTC) + m(minutes=open_min)  # 06:30 PT + n
        exp = f"2026-10-{day:02d}"
        cost = buy_px * qty * 100
        proceeds = sell_px * qty * 100
        out.append(_order(f"{tag}-b", "buy", t0, buy_px, qty, net=cost + fee, direction="debit",
                          sym=sym, strike=strike, expiry=exp))
        out.append(_order(f"{tag}-s", "sell", t0 + m(minutes=hold_min), sell_px, qty,
                          net=proceeds - fee, direction="credit", sym=sym, strike=strike,
                          expiry=exp, trigger=sell_trigger))

    trip("m1", 8, "ZORP", 25, 1.00, 1.60, 2, sell_trigger="stop")
    trip("m2", 9, "NIMB", 40, 2.00, 1.20, 1, hold_min=150)       # sells after the window
    trip("m3", 12, "ACME", 12, 3.00, 1.00, 2, fee=2.0)           # big loser
    trip("m4", 13, "ZORP", 26, 1.20, 1.50, 3)
    trip("m5", 13, "ACME", 13, 0.80, 0.60, 2, open_min=40, hold_min=5)
    return out


def build_mock_db(path: Path) -> None:
    """(Re)build the fixture DB at ``path`` via the normal import path."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    engine = get_engine(str(path))
    init_db(engine)
    import_fills(RobinhoodExport(synthetic_orders()), engine, full=True)
    add_plan(
        caller="mentor-a", contract="ZORP 25C 10/8", size_usd=200, max_loss_usd=60,
        note="synthetic plan", session_date=datetime(2026, 10, 8).date(),
        created_at=datetime(2026, 10, 8, 13, 20, tzinfo=UTC), engine=engine,
    )


def ensure_mock_db() -> Path:
    path = mock_db_path()
    if not path.exists():
        build_mock_db(path)
    return path
