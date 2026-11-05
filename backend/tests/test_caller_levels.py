"""Caller levels: read from data/levels/<date>.json, SPX/SPXW fall back to SPY."""
from __future__ import annotations

import json
from datetime import date

from app.desk import caller_levels

DAY = date(2026, 11, 4)


def _write(tmp_path, doc):
    (tmp_path / "2026-11-04.json").write_text(json.dumps(doc), encoding="utf-8")


def test_levels_rays_and_ema(tmp_path):
    _write(tmp_path, {"source": "somesh", "tickers": {
        "SPY": {"levels": [771.12, 763.37], "rays": [759.78], "ema200_1h": 764.45}}})
    got = caller_levels.load("spy", DAY, root=tmp_path)
    assert [(c.price, c.kind) for c in got] == [
        (771.12, "level"), (763.37, "level"), (759.78, "ray"), (764.45, "ema200_1h")]
    assert all(c.source == "somesh" for c in got)


def test_spxw_uses_spy_levels(tmp_path):
    _write(tmp_path, {"tickers": {"SPY": {"levels": [771.12]}}})
    assert [c.price for c in caller_levels.load("SPXW", DAY, root=tmp_path)] == [771.12]


def test_missing_file_or_ticker_is_empty(tmp_path):
    assert caller_levels.load("SPY", DAY, root=tmp_path) == []
    _write(tmp_path, {"tickers": {}})
    assert caller_levels.load("AMD", DAY, root=tmp_path) == []
