"""A caller's posted levels for a day (Somesh's morning charts), drawn on the replay chart.

Read from ``data/levels/YYYY-MM-DD.json`` (private, never committed):

    {"date": "...", "source": "somesh",
     "tickers": {"SPY": {"levels": [...], "sma200_1h": 764.45, "rays": [...]}}}

``levels`` are the caller's horizontal lines, ``sma200_1h`` the 1h 200 SMA he tags on the chart,
``rays`` the shorter lines he starts from a candle. A missing or unreadable file means no levels.
Older files call the 200 line ``ema200_1h`` (it was always an SMA); both keys are read and
emitted as kind ``sma200_1h``.
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
    sma200 = t.get("sma200_1h")
    if sma200 is None:
        sma200 = t.get("ema200_1h")  # old key, same line
    if sma200 is not None:
        out.append(CallerLevel(price=float(sma200), kind="sma200_1h", source=src))
    return out
