"""M6 task 6.0.5 — contract tests for the two training-module invariants.

Invariant 1 (answer-key hiding):
    answer_key / explanation appear ONLY in AttemptFinishResponse
    (POST /training/attempts/{id}/finish). Every other scenario or attempt
    response must be free of those keys at any nesting depth.

Invariant 2 (training trades invisible to journal analytics by default):
    Trades with context='training' are excluded from /api/analytics/summary
    and /api/trades by default.

Additional coverage:
    - Blind-mode GET blanks symbol and session_date (HTTP layer complement
      to the slicer-level test in test_lookahead.py).
    - trades.context defaults to 'real' via the ALTER TABLE migration.
    - Debrief finish persists verdict extracted from transcript (regression:
      training_store.finish_debrief was not calling summarize_transcript).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# Shared client fixture (isolated DB, same pattern as test_journal_api.py)    #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(isolated_db):
    from app.main import create_app
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _assert_no_answer_key(node, path: str = "") -> None:
    """Recursively assert that no key named 'answer_key' or 'explanation'
    exists anywhere in *node* (dicts and lists traversed).

    'explanation' is the field inside AnswerKey that would expose the secret.
    We scan for it too in case a future refactor embeds it at a different level.
    """
    FORBIDDEN = {"answer_key", "explanation"}
    if isinstance(node, dict):
        for k, v in node.items():
            current = f"{path}.{k}" if path else k
            assert k not in FORBIDDEN, (
                f"Forbidden key '{k}' found at path '{current}'"
            )
            _assert_no_answer_key(v, current)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _assert_no_answer_key(item, f"{path}[{i}]")
    # scalars: nothing to check


def _make_scenario_payload(
    *,
    blind_default: bool = False,
    symbol: str = "AAPL",
    session_date: str = "2026-06-10",
) -> dict:
    """Minimal valid ScenarioCreate payload."""
    return {
        "title": "Test drill",
        "archetype": "custom",
        "source_type": "real",
        "drill_type": "decision-point",
        "symbol": symbol,
        "session_date": session_date,
        "start_bar": 330,
        "decision_bar": 350,
        "end_bar": 430,
        "annotations": [
            {"bar": 5, "text": "Volume spike — watch for follow-through", "kind": "note"}
        ],
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Classic PDH reclaim setup with volume confirmation.",
            "management_notes": [],
        },
        "blind_default": blind_default,
    }


def _create_scenario(client: TestClient, **kwargs) -> dict:
    r = client.post("/api/training/scenarios", json=_make_scenario_payload(**kwargs))
    assert r.status_code == 200, r.text
    return r.json()


def _create_attempt(client: TestClient, scenario_id: int) -> dict:
    r = client.post("/api/training/attempts", json={"scenario_id": scenario_id})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_trade(engine, *, context: str = "real", symbol: str = "AAA",
                net_pnl: float = 100.0) -> int:
    """Insert a minimal closed trade directly into the DB with the given context."""
    from app.db.connection import init_db
    from app.db.schema import trades as trades_t

    init_db(engine)
    now = datetime(2026, 6, 10, 14, 0, 0)
    with engine.begin() as conn:
        res = conn.execute(
            trades_t.insert().values(
                symbol=symbol,
                direction="long",
                open_ts_utc=now,
                close_ts_utc=datetime(2026, 6, 10, 14, 30, 0),
                session_date=date(2026, 6, 10),
                qty=100.0,
                avg_entry=10.0,
                avg_exit=10.0 + net_pnl / 100.0,
                gross_pnl=net_pnl,
                fees=0.0,
                net_pnl=net_pnl,
                pnl_pct=net_pnl / 1000.0,
                stop_price=None,
                r_multiple=None,
                hold_time_s=1800,
                status="closed",
                simulated=False,
                context=context,
            )
        )
        return int(res.inserted_primary_key[0])


# --------------------------------------------------------------------------- #
# Invariant 1 — answer_key never leaks pre-finish                             #
# --------------------------------------------------------------------------- #

class TestAnswerKeyNeverLeaksPreFinish:

    def test_scenario_create_response_has_no_answer_key(self, client):
        """POST /training/scenarios must not return answer_key or explanation."""
        body = client.post(
            "/api/training/scenarios", json=_make_scenario_payload()
        ).json()
        _assert_no_answer_key(body)

    def test_scenario_get_response_has_no_answer_key(self, client):
        """GET /training/scenarios/{id} must not return answer_key."""
        scenario = _create_scenario(client)
        body = client.get(f"/api/training/scenarios/{scenario['id']}").json()
        _assert_no_answer_key(body)

    def test_scenario_list_response_has_no_answer_key(self, client):
        """GET /training/scenarios (list) must not contain answer_key in any item."""
        _create_scenario(client)
        _create_scenario(client)
        body = client.get("/api/training/scenarios").json()
        assert isinstance(body, list)
        assert len(body) >= 2
        for item in body:
            _assert_no_answer_key(item)

    def test_attempt_create_and_patch_have_no_answer_key(self, client):
        """POST /training/attempts and PATCH /training/attempts/{id} must not
        return answer_key in any response."""
        scenario = _create_scenario(client)
        attempt = _create_attempt(client, scenario["id"])
        _assert_no_answer_key(attempt)

        # PATCH — append an action
        patch_body = {
            "actions": [
                {
                    "bar": 20,
                    "action": "enter_long",
                    "price": 182.5,
                    "qty": 100,
                    "ts": "2026-06-10T14:20:00Z",
                }
            ]
        }
        r = client.patch(
            f"/api/training/attempts/{attempt['id']}", json=patch_body
        )
        assert r.status_code == 200, r.text
        _assert_no_answer_key(r.json())

    def test_scenario_session_response_has_no_answer_key(self, client):
        """GET /training/scenarios/{id}/session returns candles/indicators/
        annotations but must NOT contain an answer_key object."""
        # Use a real-source scenario referencing mock AAPL candles.
        scenario = _create_scenario(client, symbol="AAPL",
                                    session_date="2026-06-10")
        r = client.get(f"/api/training/scenarios/{scenario['id']}/session")
        assert r.status_code == 200, r.text
        body = r.json()
        # Session payload must have candles
        assert "candles" in body
        assert len(body["candles"]) > 0
        # Annotations are allowed (they are teaching notes, not the answer key)
        # but the answer_key object itself must not appear.
        _assert_no_answer_key(body)

    def test_attempt_finish_is_the_only_place_answer_key_appears(
        self, client, monkeypatch
    ):
        """POST /training/attempts/{id}/finish is the ONLY endpoint that returns
        answer_key with correct_actions and explanation.

        The process scorer (quant task 6.1.1) isn't implemented yet — the module
        app.analytics.training doesn't exist, so the route returns 501.  We inject
        a stub module into sys.modules so the route can import score_attempt and
        the test can verify the answer_key is exposed in the finish response.
        """
        import sys
        import types

        from app.models.training import (
            AnswerKey,
            AttemptActionType,
            AttemptFinishResponse,
        )

        # Create scenario and attempt first so we have real IDs.
        scenario = _create_scenario(client)
        attempt = _create_attempt(client, scenario["id"])

        def _stub_score(attempt_obj, scenario_full):
            return AttemptFinishResponse(
                attempt=attempt_obj,
                process_score=0.8,
                outcome_pnl=None,
                checklist=[],
                answer_key=AnswerKey(
                    correct_actions=[AttemptActionType.enter_long],
                    explanation="Classic PDH reclaim setup with volume confirmation.",
                ),
            )

        # Inject a stub analytics.training module so the lazy import in the
        # route succeeds (the real module won't exist until quant task 6.1.1).
        stub_module = types.ModuleType("app.analytics.training")
        stub_module.score_attempt = _stub_score
        monkeypatch.setitem(sys.modules, "app.analytics.training", stub_module)

        r = client.post(f"/api/training/attempts/{attempt['id']}/finish")
        assert r.status_code == 200, r.text
        body = r.json()
        # The finish response MUST contain an answer_key
        assert "answer_key" in body, "finish response is missing answer_key"
        ak = body["answer_key"]
        assert "correct_actions" in ak, "answer_key missing correct_actions"
        assert "explanation" in ak, "answer_key missing explanation"
        assert isinstance(ak["correct_actions"], list)
        assert len(ak["correct_actions"]) > 0


# --------------------------------------------------------------------------- #
# Invariant 2 — training trades invisible to journal analytics by default     #
# --------------------------------------------------------------------------- #

class TestTrainingTradesExcludedFromAnalytics:

    def test_training_trades_excluded_from_stats(self, client, isolated_db):
        """Stats endpoint (GET /api/analytics/summary) must reflect only
        non-training trades when the DB contains a mix of real/sim/training."""
        from app.db.connection import get_engine
        engine = get_engine()

        # Seed three trades: real (+100), sim (+200), training (+999)
        _seed_trade(engine, context="real", symbol="REAL", net_pnl=100.0)
        _seed_trade(engine, context="sim", symbol="SIM", net_pnl=200.0)
        _seed_trade(engine, context="training", symbol="DRILL", net_pnl=999.0)

        r = client.get("/api/analytics/summary")
        assert r.status_code == 200, r.text
        body = r.json()

        # Only real + sim trades (2 trades)
        assert body["trade_count"] == 2, (
            f"expected 2 trades (real+sim), got {body['trade_count']}"
        )
        assert body["total_pnl"] == pytest.approx(300.0), (
            f"expected total_pnl=300 (real+sim only), got {body['total_pnl']}"
        )

    def test_training_trades_excluded_from_journal_list(self, client, isolated_db):
        """GET /api/trades must not return training-context trades by default."""
        from app.db.connection import get_engine
        engine = get_engine()

        _seed_trade(engine, context="real", symbol="REAL", net_pnl=50.0)
        _seed_trade(engine, context="training", symbol="DRILL", net_pnl=999.0)

        r = client.get("/api/trades")
        assert r.status_code == 200, r.text
        trades = r.json()

        symbols = [t["symbol"] for t in trades]
        assert "REAL" in symbols, "real trade should appear in journal list"
        assert "DRILL" not in symbols, (
            "training trade must not appear in default journal list"
        )

    @pytest.mark.xfail(
        reason=(
            "No context=training query param on GET /api/trades yet — "
            "adding it is data-eng territory (follow-up task 6.0.5a). "
            "Once the param lands, remove this xfail."
        ),
        strict=False,
    )
    def test_training_trades_visible_when_context_filter_asked_for(
        self, client, isolated_db
    ):
        """GET /api/trades?context=training returns only training trades.

        The route currently has no context filter param (checked routes/trades.py).
        This test is xfail — it documents the missing capability and will auto-pass
        once data-eng adds the param.
        """
        from app.db.connection import get_engine
        engine = get_engine()

        _seed_trade(engine, context="real", symbol="REAL", net_pnl=50.0)
        _seed_trade(engine, context="training", symbol="DRILL", net_pnl=999.0)

        r = client.get("/api/trades?context=training")
        assert r.status_code == 200, r.text
        trades = r.json()
        symbols = [t["symbol"] for t in trades]
        assert "DRILL" in symbols
        assert "REAL" not in symbols


# --------------------------------------------------------------------------- #
# Blind-mode leak (HTTP layer)                                                 #
# --------------------------------------------------------------------------- #

class TestBlindMode:

    def test_blind_scenario_get_blanks_symbol_and_date(self, client):
        """GET /training/scenarios/{id} on a blind_default=True scenario must
        return symbol=null and session_date=null."""
        scenario = _create_scenario(client, blind_default=True,
                                    symbol="AAPL", session_date="2026-06-10")
        body = client.get(f"/api/training/scenarios/{scenario['id']}").json()
        assert body["symbol"] is None, (
            f"blind scenario must blank symbol, got {body['symbol']!r}"
        )
        assert body["session_date"] is None, (
            f"blind scenario must blank session_date, got {body['session_date']!r}"
        )

    def test_non_blind_scenario_get_preserves_symbol_and_date(self, client):
        """Control case: non-blind GET preserves symbol and session_date."""
        scenario = _create_scenario(client, blind_default=False,
                                    symbol="AAPL", session_date="2026-06-10")
        body = client.get(f"/api/training/scenarios/{scenario['id']}").json()
        assert body["symbol"] == "AAPL", (
            f"non-blind scenario must preserve symbol, got {body['symbol']!r}"
        )
        assert body["session_date"] == "2026-06-10", (
            f"non-blind scenario must preserve session_date, got {body['session_date']!r}"
        )


# --------------------------------------------------------------------------- #
# trades.context defaults to 'real' (ALTER TABLE migration guard)             #
# --------------------------------------------------------------------------- #

def test_trades_context_defaults_to_real(tmp_path):
    """A trade inserted without specifying context must read back as 'real'.

    This guards the ALTER TABLE … DEFAULT 'real' clause in connection.py
    (_apply_column_migrations).  We simulate a pre-M6 DB by creating the
    trades table without the context column, then running init_db to apply
    the migration, then inserting a trade via raw SQL without context.
    """
    from sqlalchemy import create_engine, text
    import app.db.connection as conn_mod
    from app.config import get_settings

    db_file = tmp_path / "migration_test.db"
    import os
    os.environ["TRADEDESK_DB_PATH"] = str(db_file)
    get_settings.cache_clear()
    conn_mod._engines.clear()

    try:
        engine = create_engine(f"sqlite:///{db_file}", future=True)

        # Step 1: Create the trades table WITHOUT the context column,
        # simulating a pre-M6 DB.
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE trades ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  symbol TEXT NOT NULL,"
                "  direction TEXT NOT NULL,"
                "  open_ts_utc DATETIME NOT NULL,"
                "  close_ts_utc DATETIME,"
                "  session_date DATE NOT NULL,"
                "  qty REAL NOT NULL,"
                "  avg_entry REAL NOT NULL,"
                "  avg_exit REAL,"
                "  gross_pnl REAL,"
                "  fees REAL NOT NULL DEFAULT 0.0,"
                "  net_pnl REAL,"
                "  pnl_pct REAL,"
                "  stop_price REAL,"
                "  r_multiple REAL,"
                "  hold_time_s INTEGER,"
                "  status TEXT NOT NULL DEFAULT 'closed',"
                "  simulated INTEGER NOT NULL DEFAULT 0"
                ")"
            ))

        # Step 2: Run init_db — this should apply the ALTER TABLE migration
        # that adds context TEXT NOT NULL DEFAULT 'real'.
        from app.db.connection import init_db
        init_db(engine)

        # Step 3: Insert a trade WITHOUT specifying context (testing the SQL DEFAULT).
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO trades "
                "(symbol, direction, open_ts_utc, close_ts_utc, session_date, "
                " qty, avg_entry, avg_exit, gross_pnl, fees, net_pnl, "
                " hold_time_s, status, simulated) "
                "VALUES "
                "('DEFAULT_TEST', 'long', '2026-06-10T14:00:00', '2026-06-10T14:30:00', "
                " '2026-06-10', 100, 10.0, 11.0, 100.0, 0.0, 100.0, 1800, 'closed', 0)"
            ))

        # Step 4: Read back and assert context == 'real'
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT context FROM trades WHERE symbol = 'DEFAULT_TEST'")
            ).first()

        assert row is not None, "inserted trade not found"
        assert row[0] == "real", (
            f"trades.context should default to 'real' via ALTER TABLE, got {row[0]!r}"
        )
    finally:
        os.environ.pop("TRADEDESK_DB_PATH", None)
        get_settings.cache_clear()
        conn_mod._engines.clear()
        engine.dispose()


# --------------------------------------------------------------------------- #
# Debrief finish persists verdict from transcript (regression fix)            #
# --------------------------------------------------------------------------- #

def _seed_trade_for_debrief(engine) -> int:
    """Insert a minimal trade and return its id for use as a debrief subject."""
    from app.db.connection import init_db
    from app.db.schema import trades as trades_t

    init_db(engine)
    now = datetime(2026, 6, 10, 14, 0, 0)
    with engine.begin() as conn:
        res = conn.execute(
            trades_t.insert().values(
                symbol="RIVN",
                direction="long",
                open_ts_utc=now,
                close_ts_utc=datetime(2026, 6, 10, 14, 30, 0),
                session_date=date(2026, 6, 10),
                qty=100.0,
                avg_entry=12.0,
                avg_exit=11.5,
                gross_pnl=-50.0,
                fees=0.0,
                net_pnl=-50.0,
                pnl_pct=-0.042,
                stop_price=11.8,
                r_multiple=None,
                hold_time_s=1800,
                status="closed",
                simulated=False,
                context="real",
            )
        )
        return int(res.inserted_primary_key[0])


def test_debrief_finish_persists_verdict_from_transcript(client, isolated_db):
    """Full form-backend debrief: POST /debriefs → 4x /turn with the last user
    reply being 'setup-failed' → POST /finish must return verdict='setup-failed'.

    Regression: before the fix, training_store.finish_debrief was not calling
    summarize_transcript on the persisted transcript, so verdict was always None.
    The fix adds a call to summarize_transcript when debrief.verdict is None.
    """
    from app.db.connection import get_engine

    engine = get_engine()
    trade_id = _seed_trade_for_debrief(engine)

    # 1. Start the debrief
    r = client.post("/api/debriefs", json={"trade_id": trade_id})
    assert r.status_code == 200, r.text
    body = r.json()
    debrief_id = body["debrief"]["id"]
    turn = body["turn"]
    assert turn["step"] == "setup"

    # 2. Reply to setup → moves to plan
    r = client.post(
        f"/api/debriefs/{debrief_id}/turn",
        json={"text": "I saw a clean PDH reclaim with volume confirmation."},
    )
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["step"] == "plan"

    # 3. Reply to plan → moves to feelings
    r = client.post(
        f"/api/debriefs/{debrief_id}/turn",
        json={"text": "Stop at 11.80, target 12.60, risking 1R."},
    )
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["step"] == "feelings"

    # 4. Reply to feelings → moves to verdict
    r = client.post(
        f"/api/debriefs/{debrief_id}/turn",
        json={"text": "calm", "fields": {"selected": ["calm"]}},
    )
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["step"] == "verdict"

    # 5. Reply to verdict with 'setup-failed' → moves to done
    r = client.post(
        f"/api/debriefs/{debrief_id}/turn",
        json={"text": "setup-failed"},
    )
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["step"] == "done"
    assert turn["done"] is True

    # 6. POST /finish — verdict must be extracted from transcript
    r = client.post(
        f"/api/debriefs/{debrief_id}/finish",
        json={"emit_scenario": False},
    )
    assert r.status_code == 200, r.text
    artifacts = r.json()

    assert artifacts["verdict"] == "setup-failed", (
        f"Expected verdict='setup-failed' from transcript parse, "
        f"got {artifacts['verdict']!r}. "
        "Regression: finish_debrief must call summarize_transcript when "
        "debrief.verdict is None."
    )
