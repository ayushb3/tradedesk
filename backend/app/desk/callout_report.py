"""Post-close report: each fill next to the nearest captured callout.

Report only. Nothing here runs live or flags a trade while the session is open. The
callouts come from the local callout listener (``app.learn.callouts``); they are private
and read in place, never copied.

Matching, per fill:
- a buy is matched to the nearest ``entry`` call on the same chart ticker (SPX/ES -> SPY,
  NDX/NQ -> QQQ) with the same direction (call -> long, put -> short);
- a sell is matched to the nearest ``trim`` / ``exit`` call on the same chart ticker;
- only calls within ``WINDOW_MIN`` minutes either side count. Anything else is unmatched.

Entry calls that no buy matched are listed as "calls not taken".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from app.learn.callouts import PROXY, Event

WINDOW_MIN = 10
PT = ZoneInfo("America/Los_Angeles")


@dataclass
class FillIn:
    trade_id: int
    label: str | None
    ticker: str
    opt_type: str        # "C" | "P" | ""
    side: str            # "B" | "S"
    qty: int
    px: float
    ts: datetime         # UTC


def _pt(ts: datetime) -> str:
    return ts.astimezone(PT).strftime("%H:%M:%S PT")


def _call_out(e: Event) -> dict:
    return {"type": e.type, "ticker": e.called, "side": e.side, "ts": e.at, "ts_pt": _pt(e.at)}


def build(day: date, fills: Sequence[FillIn], events: Sequence[Event] | None,
          window_min: int = WINDOW_MIN) -> dict:
    """The report dict (see ``app.models.desk.DayCalloutReport``). ``events`` None means no
    callouts file for the day (listener not run), which is reported, not guessed around."""
    win = timedelta(minutes=window_min)
    evs = list(events or [])
    used: set[int] = set()
    rows = []
    for f in sorted(fills, key=lambda f: f.ts):
        chart = PROXY.get(f.ticker.upper(), f.ticker.upper())
        if f.side == "B":
            want = {"C": "long", "P": "short"}.get(f.opt_type)
            pool = [(i, e) for i, e in enumerate(evs) if e.type == "entry"
                    and e.ticker == chart and (want is None or e.side == want)]
        else:
            pool = [(i, e) for i, e in enumerate(evs) if e.type in ("trim", "exit")
                    and e.ticker == chart]
        pool = [(i, e) for i, e in pool if abs(f.ts - e.at) <= win]
        best = min(pool, key=lambda ie: abs(f.ts - ie[1].at), default=None)
        if best is not None and f.side == "B":
            used.add(best[0])
        rows.append({
            "trade_id": f.trade_id, "label": f.label, "ticker": f.ticker, "side": f.side,
            "qty": f.qty, "px": f.px, "ts": f.ts, "ts_pt": _pt(f.ts),
            "callout": _call_out(best[1]) if best else None,
            # positive: the fill came after the call
            "gap_s": int((f.ts - best[1].at).total_seconds()) if best else None,
        })
    not_taken = [_call_out(e) for i, e in enumerate(evs) if e.type == "entry" and i not in used]
    matched = sum(1 for r in rows if r["callout"])
    return {
        "date": day, "callouts_found": events is not None, "window_min": window_min,
        "n_callouts": len(evs), "matched": matched, "unmatched": len(rows) - matched,
        "fills": rows, "calls_not_taken": not_taken,
    }


def fills_from_positions(positions: Iterable) -> list[FillIn]:
    """Flatten the day page's positions (``PositionOut``) into fills."""
    out = []
    for p in positions:
        for f in p.fills:
            out.append(FillIn(p.trade_id, p.label, p.ticker, p.type, f.side, f.qty, f.px, f.ts))
    return out
