"""Free replay: GET /replay/option-bars serves one 0DTE contract's premium bars. Mock, no network."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

DAY = "2026-10-13"
URL = "/api/replay/option-bars"


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _get(client, **over):
    params = {"underlying": "SPY", "type": "call", "strike": 600, "date": DAY, "timeframe": "1m"}
    params.update(over)
    return client.get(URL, params=params)


def _on_day(candles) -> bool:
    et = ZoneInfo("America/New_York")
    want = date.fromisoformat(DAY)
    return all(datetime.fromisoformat(c["ts"]).astimezone(et).date() == want for c in candles)


def test_spy_call_happy_path(client):
    r = _get(client)
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True and j["reason"] is None
    assert j["underlying"] == "SPY" and j["option_type"] == "call"
    assert j["strike"] == 600 and j["expiry"] == DAY
    assert j["contract_key"] == "SPY_261013_C_600"
    assert j["symbol"] == "SPY 600C 10/13"
    assert len(j["candles"]) > 60 and _on_day(j["candles"])


def test_put_qqq_and_spxw(client):
    put = _get(client, type="PUT", strike=598.5).json()
    assert put["available"] and put["option_type"] == "put" and put["contract_key"].endswith("_P_598.5")
    qqq = _get(client, underlying="qqq", strike=500).json()
    assert qqq["available"] and qqq["underlying"] == "QQQ" and qqq["candles"]
    spxw = _get(client, underlying="SPXW", strike=7575).json()
    assert spxw["available"] and spxw["underlying"] == "SPXW" and spxw["candles"]


@pytest.mark.parametrize(
    "over",
    [
        {"underlying": "TSLA"},
        {"type": "straddle"},
        {"strike": 0},
        {"strike": -5},
        {"strike": 600.25},
        {"underlying": "SPXW", "strike": 7572},
        {"timeframe": "3m"},
    ],
)
def test_bad_params_400(client, over):
    r = _get(client, **over)
    assert r.status_code == 400
    assert r.json()["detail"]


class _Provider:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def get_option_bars(self, contract, day, timeframe="1m"):
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.parametrize("prov", [_Provider(None), _Provider([]), _Provider(exc=RuntimeError("boom"))])
def test_provider_none_or_error_degrades(client, prov):
    from app.api.deps import provider_dep

    client.app.dependency_overrides[provider_dep] = lambda: prov
    r = _get(client)
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False and j["candles"] == [] and j["reason"]
    assert j["contract_key"] == "SPY_261013_C_600"
    if prov.exc is None:
        assert j["reason"] == "no option prints for this contract on that day"


def test_spxw_session_works_in_mock(client):
    r = client.get("/api/replay/session", params={"symbol": "SPXW", "date": DAY})
    assert r.status_code == 200
    assert r.json()["candles"]
