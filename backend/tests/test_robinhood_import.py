"""Robinhood option-order export -> fills -> round-trip trades.

Fixtures are synthetic orders shaped like robin_stocks ``get_all_option_orders``
output (the real export never enters the repo).
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.schema import fills as fills_t
from app.ingest.imports import MixedFillSourcesError, import_fills
from app.ingest.query import list_trades
from app.ingest.robinhood import RobinhoodExport, fills_from_option_orders
from app.models.trades import Fill, Side

UTC = timezone.utc
T0 = datetime(2026, 10, 6, 13, 45, tzinfo=UTC)  # 09:45 ET


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _order(oid, side, executions, *, net, direction, symbol="SPY", strike="680.0000",
           opt="call", expiry="2026-10-06", state="filled", trigger="immediate",
           otype="limit", created=None, updated=None, stop_price=None, legs=None):
    """executions: [(seconds after T0, price, qty)]."""
    qty = sum(q for _, _, q in executions)
    created = created or (T0 + timedelta(seconds=executions[0][0] if executions else 0))
    leg = {
        "side": side, "position_effect": "open" if side == "buy" else "close",
        "option_type": opt, "strike_price": strike, "expiration_date": expiry,
        "executions": [
            {"id": f"{oid}-x{i}", "price": f"{px:.8f}", "quantity": f"{q:.5f}",
             "timestamp": _iso(T0 + timedelta(seconds=s))}
            for i, (s, px, q) in enumerate(executions)
        ],
    }
    return {
        "id": oid, "account_number": "999999999", "chain_symbol": symbol,
        "state": state, "trigger": trigger, "type": otype, "stop_price": stop_price,
        "processed_quantity": f"{qty:.5f}", "net_amount": f"{net:.2f}",
        "net_amount_direction": direction, "trade_value_multiplier": "100.0000",
        "created_at": _iso(created), "updated_at": _iso(updated or created),
        "legs": legs if legs is not None else [leg],
    }


def _round_trip():
    """Buy 2 @ 1.50 in two executions, sell 2 @ 2.00 on a stop. Fees 1.04 + 1.06."""
    buy = _order("b1", "buy", [(0, 1.50, 1), (2, 1.50, 1)], net=301.04, direction="debit")
    sell = _order("s1", "sell", [(600, 2.00, 2)], net=398.94, direction="credit",
                  trigger="stop", stop_price="2.0500")
    return [buy, sell]


def _count_fills(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(fills_t)).scalar()


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
def test_one_fill_per_order_with_vwap_first_time_and_contract():
    buy = _order("b1", "buy", [(5, 1.40, 1), (1, 1.60, 3)], net=620.52, direction="debit")
    [f] = fills_from_option_orders([buy]).fills

    assert f.provider_fill_id == "rh-b1"
    assert f.account == "ROBINHOOD"
    assert (f.symbol, f.side, f.qty) == ("SPY", Side.buy, 4)
    assert f.price == pytest.approx((1.40 + 3 * 1.60) / 4)
    assert f.ts == T0 + timedelta(seconds=1)  # first execution, not first listed
    assert (f.option_type, f.strike, f.expiry, f.multiplier) == ("CALL", 680.0, "2026-10-06", 100.0)
    assert f.fees == pytest.approx(0.52)


def test_fees_come_from_net_amount():
    buy, sell = fills_from_option_orders(_round_trip()).fills
    assert buy.fees == pytest.approx(1.04)
    assert sell.fees == pytest.approx(1.06)
    assert buy.raw_json["net_amount"] == -301.04
    assert sell.raw_json["net_amount"] == 398.94


def test_raw_json_keeps_trade_facts_not_account_numbers():
    _, sell = fills_from_option_orders(_round_trip()).fills
    assert sell.raw_json["order_type"] == "stop"
    assert sell.raw_json["stop_price"] == 2.05
    assert "999999999" not in json.dumps(sell.raw_json)


def test_unfilled_orders_skipped_partial_cancel_kept_multi_leg_counted():
    unfilled = _order("c0", "buy", [], net=0, direction="debit", state="cancelled")
    unfilled["processed_quantity"] = "0.00000"
    partial = _order("c1", "buy", [(0, 1.0, 1)], net=100.5, direction="debit", state="cancelled")
    spread = _order("sp", "buy", [(0, 1.0, 1)], net=100.0, direction="debit")
    spread["legs"] = spread["legs"] * 2

    parsed = fills_from_option_orders([unfilled, partial, spread])
    assert [f.provider_fill_id for f in parsed.fills] == ["rh-c1"]
    assert parsed.skipped_multi_leg == 1


def test_cancel_and_replace_stop_chain_is_one_trailed_stop():
    def stop(oid, created_s, updated_s, state, executions=()):
        o = _order(oid, "sell", list(executions) or [(created_s, 0, 0)], net=0,
                   direction="credit", state=state, trigger="stop",
                   created=T0 + timedelta(seconds=created_s),
                   updated=T0 + timedelta(seconds=updated_s))
        if not executions:
            o["processed_quantity"] = "0.00000"
            o["legs"][0]["executions"] = []
        return o

    orders = [
        stop("st1", 0, 100, "cancelled"),
        stop("st2", 102, 200, "cancelled"),   # replaces st1 2s later
        stop("st3", 203, 300, "filled", [(300, 2.0, 1)]),  # replaces st2
        stop("st4", 5000, 5100, "cancelled"),  # a separate, pulled stop
    ]
    orders[2]["net_amount"] = "199.00"
    [f] = fills_from_option_orders(orders).fills
    assert f.provider_fill_id == "rh-st3"
    assert f.raw_json["stop_moves"] == 2


# --------------------------------------------------------------------------- #
# Import into the trades table
# --------------------------------------------------------------------------- #
def test_round_trip_net_pnl_equals_cash(temp_engine):
    res = import_fills(RobinhoodExport(_round_trip()), temp_engine)
    assert res == {"fills_imported": 2, "trades_built": 1}

    [t] = list_trades(simulated=False, engine=temp_engine)
    assert t.contract_label == "SPY 680C 10/06"
    assert t.qty == 2
    assert t.gross_pnl == pytest.approx(100.0)
    assert t.net_pnl == pytest.approx(398.94 - 301.04)
    assert t.session_date.isoformat() == "2026-10-06"


def test_reimport_overlapping_export_does_not_duplicate(temp_engine):
    import_fills(RobinhoodExport(_round_trip()), temp_engine)
    later = _order("b2", "buy", [(3600, 1.0, 1)], net=100.52, direction="debit")
    res = import_fills(RobinhoodExport(_round_trip() + [later]), temp_engine)

    assert res["fills_imported"] == 1
    assert _count_fills(temp_engine) == 3
    assert len(list_trades(simulated=False, engine=temp_engine)) == 2


def test_from_file(tmp_path, temp_engine):
    p = tmp_path / "raw-option-orders-2026-10-06.json"
    p.write_text(json.dumps(_round_trip()))
    assert import_fills(RobinhoodExport.from_file(p), temp_engine)["trades_built"] == 1


# --------------------------------------------------------------------------- #
# Source isolation
# --------------------------------------------------------------------------- #
class _Static:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, since=None):
        return list(self.fills)


def _plain_fill(pfid, side, minute, price=10.0):
    return Fill(provider_fill_id=pfid, account="X", symbol="RIVN", side=side, qty=1,
                price=price, ts=datetime(2026, 6, 10, 14, minute, tzinfo=UTC))


def test_robinhood_import_clears_demo_fills(temp_engine):
    import_fills(_Static([_plain_fill("mock-1", Side.buy, 0), _plain_fill("mock-2", Side.sell, 5)]),
                 temp_engine)
    import_fills(RobinhoodExport(_round_trip()), temp_engine)
    assert {t.symbol for t in list_trades(simulated=False, engine=temp_engine)} == {"SPY"}


def test_demo_import_refused_once_real_fills_exist(temp_engine):
    import_fills(RobinhoodExport(_round_trip()), temp_engine)
    with pytest.raises(MixedFillSourcesError):
        import_fills(_Static([_plain_fill("mock-1", Side.buy, 0)]), temp_engine)
    assert _count_fills(temp_engine) == 2


def test_live_full_refresh_keeps_robinhood_fills(temp_engine):
    import_fills(RobinhoodExport(_round_trip()), temp_engine)
    live = _Static([_plain_fill("wb-1", Side.buy, 0), _plain_fill("wb-2", Side.sell, 5)])
    import_fills(live, temp_engine, full=True)
    assert {t.symbol for t in list_trades(simulated=False, engine=temp_engine)} == {"SPY", "RIVN"}


def test_idless_fills_get_stable_ids_and_do_not_duplicate(temp_engine):
    same = [_plain_fill(None, Side.buy, 0), _plain_fill(None, Side.buy, 0),
            _plain_fill(None, Side.sell, 5)]
    import_fills(_Static(same), temp_engine)
    import_fills(_Static(same), temp_engine)
    assert _count_fills(temp_engine) == 3
    [t] = list_trades(simulated=False, engine=temp_engine)
    assert t.qty == 2  # peak position: the two identical buys are both kept


# --------------------------------------------------------------------------- #
# Read-only guarantee
# --------------------------------------------------------------------------- #
def test_importer_has_no_network_or_order_entry():
    root = Path(__file__).resolve().parent.parent
    for rel in ("app/ingest/robinhood.py", "scripts/import_robinhood.py"):
        src = (root / rel).read_text(encoding="utf-8")
        imported = {
            (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        assert not any(m.split(".")[0] in {"robin_stocks", "requests", "httpx", "urllib", "socket"}
                       for m in imported), rel
        for word in ("place_order", "cancel_order", "order_buy", "order_sell"):
            assert word not in src, (rel, word)


# --------------------------------------------------------------------------- #
# API timestamps carry UTC (browser must not read them as local time)
# --------------------------------------------------------------------------- #
def test_api_times_are_utc_with_z(temp_engine):
    import_fills(RobinhoodExport(_round_trip()), temp_engine)
    [t] = list_trades(simulated=False, engine=temp_engine)
    body = t.model_dump(mode="json")
    assert body["open_ts"] == "2026-10-06T13:45:00Z"  # 09:45 ET
    assert body["close_ts"] == "2026-10-06T13:55:00Z"
    assert t.open_ts == datetime(2026, 10, 6, 13, 45)  # Python value unchanged (naive UTC)


def test_utc_iso_converts_aware_values():
    from zoneinfo import ZoneInfo
    from app.models.utc import utc_iso
    et = datetime(2026, 10, 6, 9, 45, tzinfo=ZoneInfo("America/New_York"))
    assert utc_iso(et) == "2026-10-06T13:45:00Z"
