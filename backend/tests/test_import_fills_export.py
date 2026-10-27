"""POST /api/import/fills in mock mode loads the newest local Robinhood export."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.desk_mcp import mock, writes


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def _export_dir(tmp_path, monkeypatch):
    d = tmp_path / "rh-export" / "out"
    d.mkdir(parents=True)
    monkeypatch.setenv(writes.EXPORT_DIR_ENV, str(d))
    return d


def _write(d, name, orders, mtime):
    f = d / name
    f.write_text(json.dumps(orders), encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return f


def test_export_present_imports_newest_file(client, tmp_path, monkeypatch):
    d = _export_dir(tmp_path, monkeypatch)
    orders = mock.synthetic_orders()
    _write(d, "raw-option-orders-2026-10-01.json", [], 1_000_000)  # older, empty
    _write(d, "raw-option-orders-2026-10-02.json", orders, 2_000_000)
    r = client.post("/api/import/fills")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "robinhood_export"
    assert body["export_file"] == "raw-option-orders-2026-10-02.json"
    assert body["fills_imported"] > 0 and body["trades_built"] > 0
    assert body["days_touched"] >= 1
    # audit row written, with no path leak
    from app.db.connection import get_engine
    from app.desk_mcp import tools

    ctx = tools.Ctx("mock", "", get_engine(), None)
    row = writes.audit_rows(ctx)[0]
    assert row["tool"] == "import_robinhood" and row["result"] == "ok"
    assert str(tmp_path) not in json.dumps(row)
    # a second press is idempotent and still works (no mixed-source 409)
    assert client.post("/api/import/fills").status_code == 200


def test_no_export_with_real_fills_is_clear_404(client, tmp_path, monkeypatch):
    d = _export_dir(tmp_path, monkeypatch)
    f = _write(d, "raw-option-orders-2026-10-02.json", mock.synthetic_orders(), 2_000_000)
    assert client.post("/api/import/fills").status_code == 200
    f.unlink()
    r = client.post("/api/import/fills")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "no Robinhood export found in out" in detail
    assert "run the export first" in detail
    assert str(tmp_path) not in detail


def test_no_export_empty_db_keeps_mock_import(client, tmp_path, monkeypatch):
    _export_dir(tmp_path, monkeypatch)  # exists but empty
    r = client.post("/api/import/fills")
    assert r.status_code == 200, r.text
    assert r.json()["trades_built"] == 10
    assert "source" not in r.json()


def test_non_matching_names_ignored(client, tmp_path, monkeypatch):
    d = _export_dir(tmp_path, monkeypatch)
    _write(d, "account.json", mock.synthetic_orders(), 9_000_000)
    _write(d, "raw-option-orders-1.txt", mock.synthetic_orders(), 9_000_000)
    _write(d, "raw-option-orders-2026-10-01.json", mock.synthetic_orders(), 1_000_000)
    r = client.post("/api/import/fills")
    assert r.status_code == 200
    assert r.json()["export_file"] == "raw-option-orders-2026-10-01.json"


def test_newest_export_file_none_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(writes.EXPORT_DIR_ENV, str(tmp_path / "nope"))
    assert writes.newest_export_file() is None
