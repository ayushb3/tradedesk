"""Robinhood option fills from a read-only order export.

Input is the raw option-order JSON written by the out-of-repo read-only export
(`rh-export/export.py` -> `out/raw-option-orders-<start>.json`, a list of
robin_stocks ``get_all_option_orders`` dicts). This module only reads that file;
it makes no network calls and has no order-entry code (read-only guarantee).

Mapping, one `Fill` per order that executed anything:

- ``provider_fill_id`` = ``"rh-" + order id``. Order ids are stable across
  exports, so re-importing an overlapping export never duplicates fills.
- ``ts`` = the order's first execution time; ``qty`` = executed quantity;
  ``price`` = execution VWAP (per-share premium).
- ``fees`` = the gap between gross premium and ``net_amount`` (Robinhood's
  ``net_amount`` is the exact cash that moved, after fees), so a round-trip's
  ``net_pnl`` equals the real cash result.
- Contract identity matches `imports._contract_key`: ``option_type`` CALL/PUT,
  ``strike``, ``expiry`` ISO date, ``multiplier`` from ``trade_value_multiplier``.
- Stop orders keep ``order_type="stop"`` in ``raw_json``. A stop that is
  cancelled and replaced within seconds by another stop on the same contract is
  one stop being trailed, not a pulled stop; the order that ends a chain carries
  ``stop_moves`` (number of replacements).
- Multi-leg orders are skipped and counted (spreads are issue #15).
- Account numbers and other identifiers never land in ``raw_json``.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.models.trades import Fill, Side

ACCOUNT = "ROBINHOOD"
FILL_ID_PREFIX = "rh-"

# Cancel-and-replace window for a trailed stop: the replacement is created
# between 5s before and 10s after the previous stop's last update.
_TRAIL_GAP_S = (-5.0, 10.0)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _order_type(order: dict) -> str:
    return "stop" if order.get("trigger") == "stop" else str(order.get("type"))


def _stop_moves(orders: list[dict]) -> dict[str, int]:
    """order id -> number of cancel-and-replace moves, for the last stop of each chain."""
    by_contract: dict[tuple, list[dict]] = defaultdict(list)
    for o in orders:
        if o.get("trigger") == "stop" and len(o.get("legs") or []) == 1:
            leg = o["legs"][0]
            key = (o.get("chain_symbol"), leg.get("expiration_date"),
                   leg.get("option_type"), leg.get("strike_price"), leg.get("side"))
            by_contract[key].append(o)

    moves: dict[str, int] = {}
    for stops in by_contract.values():
        stops.sort(key=lambda o: o["created_at"])
        chain = [stops[0]]
        for o in stops[1:] + [None]:
            prev = chain[-1]
            if o is not None and prev.get("state") == "cancelled":
                gap = (_ts(o["created_at"]) - _ts(prev["updated_at"])).total_seconds()
                if _TRAIL_GAP_S[0] <= gap <= _TRAIL_GAP_S[1]:
                    chain.append(o)
                    continue
            moves[prev["id"]] = len(chain) - 1
            chain = [o]
    return moves


@dataclass
class ParsedExport:
    fills: list[Fill] = field(default_factory=list)
    skipped_multi_leg: int = 0


def fills_from_option_orders(orders: list[dict]) -> ParsedExport:
    """Map raw Robinhood option orders to `Fill`s (see module docstring)."""
    out = ParsedExport()
    moves = _stop_moves(orders)
    for o in orders:
        if float(o.get("processed_quantity") or 0) <= 0:
            continue
        legs = o.get("legs") or []
        if len(legs) != 1:
            out.skipped_multi_leg += 1
            continue
        leg = legs[0]
        executions = leg.get("executions") or []
        if not executions:
            continue

        qty = sum(float(e["quantity"]) for e in executions)
        price = sum(float(e["price"]) * float(e["quantity"]) for e in executions) / qty
        multiplier = float(o.get("trade_value_multiplier") or 100.0)
        side = Side.buy if leg["side"] == "buy" else Side.sell

        gross = qty * price * multiplier
        net = float(o["net_amount"])
        debit = o.get("net_amount_direction") == "debit"
        fees = round(max(net - gross if debit else gross - net, 0.0), 2)

        raw = {
            "source": "robinhood-export",
            "order_id": o["id"],
            "order_type": _order_type(o),
            "position_effect": leg.get("position_effect"),
            "executions": len(executions),
            "net_amount": round(-net if debit else net, 2),
        }
        if o.get("stop_price") is not None:
            raw["stop_price"] = float(o["stop_price"])
        if o["id"] in moves:
            raw["stop_moves"] = moves[o["id"]]

        out.fills.append(Fill(
            provider_fill_id=FILL_ID_PREFIX + o["id"],
            account=ACCOUNT,
            symbol=o["chain_symbol"],
            side=side,
            qty=qty,
            price=price,
            ts=min(_ts(e["timestamp"]) for e in executions),
            fees=fees,
            multiplier=multiplier,
            option_type=str(leg["option_type"]).upper(),
            strike=float(leg["strike_price"]),
            expiry=leg["expiration_date"],
            simulated=False,
            raw_json=raw,
        ))
    out.fills.sort(key=lambda f: f.ts)
    return out


class RobinhoodExport:
    """Duck-typed fill source for `imports.import_fills`.

    The export is a static file, so ``since`` is ignored: every fill is returned
    and the importer's ``provider_fill_id`` dedupe makes the overlap free.
    """

    def __init__(self, orders: list[dict]) -> None:
        self.parsed = fills_from_option_orders(orders)

    @classmethod
    def from_file(cls, path: str | Path) -> "RobinhoodExport":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def get_fills(self, since: datetime | None = None) -> list[Fill]:
        return list(self.parsed.fills)
