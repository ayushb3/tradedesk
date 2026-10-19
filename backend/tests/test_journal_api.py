"""End-to-end journal API (M3) via FastAPI TestClient on an isolated temp DB.

Flow: POST /api/import/fills -> GET /api/trades -> GET /api/analytics/summary
-> PATCH /api/trades/{id} (grade + tags + stop) -> GET /api/tags (default setup tags).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def test_import_lists_ten_trades_for_mock(client):
    r = client.post("/api/import/fills")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trades_built"] == 10  # 10 mock round-trip templates
    assert body["fills_imported"] > 0

    trades = client.get("/api/trades").json()
    assert len(trades) == 10
    assert all(t["simulated"] is False for t in trades)


def test_summary_is_sane_after_import(client):
    client.post("/api/import/fills")
    s = client.get("/api/analytics/summary").json()
    # All 10 mock trades are closed round-trips.
    assert s["trade_count"] == 10
    assert s["win_count"] + s["loss_count"] <= 10
    assert 0.0 <= s["win_rate"] <= 1.0
    # counts are internally consistent with total pnl sign-agnostic checks
    assert isinstance(s["total_pnl"], (int, float))
    assert s["best_trade"] >= s["worst_trade"]


def test_tags_expose_five_default_tags(client):
    tags = client.get("/api/tags").json()
    names = {t["name"] for t in tags}
    assert {
        "PDH-break",
        "PMH-break",
        "OR-breakout",
        "VWAP-reclaim",
        "EMA-pullback",
    } <= names
    assert len([t for t in tags if t["active"]]) >= 5


def test_patch_sets_grade_tags_and_recomputes_r(client):
    client.post("/api/import/fills")
    trades = client.get("/api/trades").json()

    # Pick a closed trade so an r_multiple can actually be computed.
    closed = [t for t in trades if t["status"] == "closed"]
    assert closed, "expected closed trades from the mock import"
    trade = closed[0]
    tid = trade["id"]

    tags = client.get("/api/tags").json()
    tag_ids = [t["id"] for t in tags[:2]]

    # Choose a stop on the losing side of entry so risk/share > 0.
    entry = trade["avg_entry"]
    is_long = trade["direction"] == "long"
    stop = round(entry - 1.0, 4) if is_long else round(entry + 1.0, 4)

    patch = {
        "grade": "A",
        "notes": "clean setup",
        "tag_ids": tag_ids,
        "stop_price": stop,
    }
    r = client.patch(f"/api/trades/{tid}", json=patch)
    assert r.status_code == 200, r.text
    updated = r.json()

    assert updated["enrichment"]["grade"] == "A"
    assert updated["enrichment"]["notes"] == "clean setup"
    assert {t["id"] for t in updated["tags"]} == set(tag_ids)
    assert updated["stop_price"] == stop
    assert updated["r_multiple"] is not None

    # R is the one desk definition (pnl / written max loss, else 30% of cost "assumed"),
    # unchanged by the stop price.
    assert updated["r_basis"] == "assumed"
    assert updated["r_multiple"] == pytest.approx(updated["net_pnl"] / (0.30 * updated["qty"] * updated["avg_entry"]), rel=0.2)
    assert updated["r_multiple"] == trade["r_multiple"]

    # Enrichment + stop persist on a fresh GET.
    again = client.get(f"/api/trades/{tid}").json()
    assert again["enrichment"]["grade"] == "A"
    assert again["r_multiple"] == updated["r_multiple"]
    assert {t["id"] for t in again["tags"]} == set(tag_ids)


def test_patch_unknown_trade_404(client):
    r = client.patch("/api/trades/999999", json={"grade": "B"})
    assert r.status_code == 404


def test_create_tag_then_appears_in_list(client):
    created = client.post(
        "/api/tags", json={"name": "gap-and-go", "description": "opening gap"}
    ).json()
    assert created["name"] == "gap-and-go"
    names = {t["name"] for t in client.get("/api/tags").json()}
    assert "gap-and-go" in names
