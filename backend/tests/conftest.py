"""Shared pytest fixtures for the TradeDesk backend test suite.

DB isolation: tests either pass an explicit temp-file engine to the ingest
functions, or (for the API smoke test) point ``TRADEDESK_DB_PATH`` at a temp
file and clear the cached settings/engine so no real DB is ever touched.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine

UTC = ZoneInfo("UTC")

# A fixed weekday used across the suite. June => EDT (UTC-4), so 09:30 ET == 13:30 UTC.
TEST_DAY = date(2026, 6, 10)  # Wednesday
PRIOR_DAY = date(2026, 6, 9)  # Tuesday (prior trading day)


@pytest.fixture(autouse=True)
def _force_mock_mode(monkeypatch):
    """Force ``TRADEDESK_MODE=mock`` for every test, regardless of ``.env``.

    The app reads its data-provider mode from the repo-root ``.env`` so the
    running app can be pointed at the live Webull API (``TRADEDESK_MODE=webull``)
    for real use. Tests must NEVER hit the live API — it is rate-limited,
    needs a verified token, and makes the suite non-deterministic. This autouse
    fixture pins the mode to ``mock`` (via env var, which overrides the ``.env``
    file value in pydantic-settings) and clears the cached ``Settings`` so the
    override takes effect. Tests that need live behaviour construct
    ``WebullProvider`` directly with fixtures/monkeypatched clients — they do not
    go through ``get_provider()``/mode, so this pin does not affect them.
    """
    monkeypatch.setenv("TRADEDESK_MODE", "mock")

    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def temp_engine(tmp_path):
    """A throwaway SQLite engine backed by a temp file (never the real DB)."""
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point the whole app at a temp DB and reset cached settings/engines.

    Used by the API smoke test where the route calls ``save_sim_trade`` with the
    default (settings-derived) engine.
    """
    db_file = tmp_path / "api.db"
    monkeypatch.setenv("TRADEDESK_DB_PATH", str(db_file))

    from app.config import get_settings
    import app.db.connection as conn

    get_settings.cache_clear()
    conn._engines.clear()
    yield db_file
    get_settings.cache_clear()
    conn._engines.clear()


def mock_candles(symbol: str, day: date):
    """Deterministic MockProvider candles for ``(symbol, day)``."""
    from app.config import Settings
    from app.providers.mock import MockProvider

    return MockProvider(Settings()).get_candles(symbol, day)
