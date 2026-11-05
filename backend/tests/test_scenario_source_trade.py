"""ScenarioPublic.source_trade_id: Learn's "From your trades" lists Critique/Debrief drills by it.
Blind scenarios never reveal it (it would identify the tape). Synthetic data only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def _payload(**over) -> dict:
    p = {
        "title": "Synthetic debrief drill", "archetype": "custom", "source_type": "debrief",
        "drill_type": "decision-point", "symbol": "SYN", "session_date": "2030-01-02",
        "start_bar": 0, "decision_bar": 5, "end_bar": 50, "annotations": [],
        "answer_key": {"correct_actions": ["wait"], "explanation": "Synthetic.",
                       "management_notes": []},
        "source_trade_id": 4242, "blind_default": False,
    }
    p.update(over)
    return p


def test_source_trade_id_is_public_unless_blind(client):
    made = client.post("/api/training/scenarios", json=_payload()).json()
    assert made["source_trade_id"] == 4242
    listed = {s["id"]: s for s in client.get("/api/training/scenarios?active=true").json()}
    assert listed[made["id"]]["source_trade_id"] == 4242
    blind = client.post("/api/training/scenarios", json=_payload(blind_default=True)).json()
    assert blind["source_trade_id"] is None and blind["symbol"] is None
    plain = client.post("/api/training/scenarios",
                        json=_payload(source_type="real", source_trade_id=None)).json()
    assert plain["source_trade_id"] is None
