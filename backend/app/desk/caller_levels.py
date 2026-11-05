"""A caller's posted levels for a day (Somesh's morning charts), drawn on the replay chart.

Read from ``data/levels/YYYY-MM-DD.json`` (private, never committed):

    {"date": "...", "source": "somesh",
     "tickers": {"SPY": {"levels": [...], "ema200_1h": 764.45, "rays": [...]}}}

``levels`` are the caller's horizontal lines, ``ema200_1h`` the 1h 200 EMA he tags on the chart,
``rays`` the shorter lines he starts from a candle. A missing or unreadable file means no levels.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.config import _REPO_ROOT
from app.models.replay import CallerLevel

LEVELS_DIR = _REPO_ROOT / "data" / "levels"
# index trades chart on their ETF (the course trades SPX off SPY levels)
PROXY = {"SPX": "SPY", "SPXW": "SPY"}


def load(symbol: str, day: date, root: Path = LEVELS_DIR) -> list[CallerLevel]:
    f = root / f"{day.isoformat()}.json"
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sym = symbol.upper()
    tickers = doc.get("tickers") or {}
    t = tickers.get(sym) or tickers.get(PROXY.get(sym, "")) or {}
    src = doc.get("source") or "caller"
    out = [CallerLevel(price=float(p), kind="level", source=src) for p in t.get("levels") or []]
    out += [CallerLevel(price=float(p), kind="ray", source=src) for p in t.get("rays") or []]
    if t.get("ema200_1h") is not None:
        out.append(CallerLevel(price=float(t["ema200_1h"]), kind="ema200_1h", source=src))
    return out
