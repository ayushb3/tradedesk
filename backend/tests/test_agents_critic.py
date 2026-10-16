"""Trade Critic tests (M9a, issue #58, R6–R11).

Two layers:

- ENDPOINT (`POST /api/agents/critic`, `GET /api/agents/status`) through a
  TestClient on an isolated DB: the `llm=none` degradation (never 500), the
  claude-cli happy path (canned run_fn → 200 available, result validates,
  session persisted), and the 404 for an unknown trade.
- UNIT (`critique_trade` with a hand-built KnowledgeContext): the R3 citation
  drift-guard — an in-context citation is resolved to the KnowledgeSearchHit while an
  out-of-context `source_citation_lesson_id` is demoted to None (unverified).

The claude-cli seam is driven exactly like the M8c sheet-pass tests: force
`TRADEDESK_LLM=claude-cli`, reset the binary-availability cache, and feed a
canned `run_fn` that returns the CLI JSON envelope whose `result` is the
critic-payload JSON string.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agents.critic import CriticInputs, critique_trade
from app.config import Settings
from app.llm.base import DebriefContext
from app.models.agents import KnowledgeContext, CriticResult
from app.models.knowledge import KnowledgeSearchHit
from app.models.trades import Direction, Trade, TradeStatus

from .conftest import TEST_DAY


# --------------------------------------------------------------------------- #
# Canned claude-cli runner (mirrors test_knowledge_sheets_pass._canned_run_fn)
# --------------------------------------------------------------------------- #

def _wrap_in_cli_envelope(payload_json: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": payload_json,
    })


def _reset_binary_cache():
    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None


def _canned_run_fn(payload: dict):
    """run_fn returning --version success + a fixed critic payload envelope."""
    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(json.dumps(payload)),
            stderr="",
        )
    return _run


def _hit(lesson_id: int, *, text: str, title: str = "Lesson") -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        text=text,
        score=0.9,
        lesson_id=lesson_id,
        lesson_title=title,
        module="7",
        seq=lesson_id,
        char_start=0,
        char_end=len(text),
        citation=f"Module 7 · Lesson {lesson_id:03d} — {title}",
    )


def _trade(trade_id: int = 1) -> Trade:
    from datetime import datetime, timezone

    return Trade(
        id=trade_id,
        symbol="AAPL",
        direction=Direction.long,
        open_ts=datetime(2026, 6, 10, 13, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
        session_date=TEST_DAY,
        qty=100.0,
        avg_entry=10.0,
        avg_exit=11.5,
        net_pnl=150.0,
        status=TradeStatus.closed,
        simulated=True,
    )


# --------------------------------------------------------------------------- #
# Endpoint fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(isolated_db, monkeypatch):
    """TestClient with TRADEDESK_LLM=none (the degrade-clean baseline). The repo
    `.env` sets claude-cli, so pin `none` explicitly and clear cached settings."""
    monkeypatch.setenv("TRADEDESK_LLM", "none")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def client_cli(isolated_db, monkeypatch):
    """TestClient with TRADEDESK_LLM=claude-cli and a canned runner patched in."""
    monkeypatch.setenv("TRADEDESK_LLM", "claude-cli")
    from app.config import get_settings

    get_settings.cache_clear()
    _reset_binary_cache()
    from app.main import create_app

    return TestClient(create_app())


def _seed_sim_trade(client) -> int:
    payload = {
        "symbol": "AAPL",
        "direction": "long",
        "session_date": str(TEST_DAY),
        "entries": [{"ts": "2026-06-10T13:30:00Z", "qty": 100, "price": 10.00}],
        "exits": [{"ts": "2026-06-10T14:00:00Z", "qty": 100, "price": 11.50}],
        "stop_price": 9.00,
    }
    post = client.post("/api/sim/trades", json=payload)
    assert post.status_code == 200, post.text
    return post.json()["id"]


# --------------------------------------------------------------------------- #
# llm=none degradation (never 500)
# --------------------------------------------------------------------------- #

def test_status_unavailable_under_llm_none(client):
    r = client.get("/api/agents/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["llm"] == "none"


def test_critic_unavailable_under_llm_none(client):
    # Any trade_id: the availability gate short-circuits before touching the DB.
    r = client.post("/api/agents/critic", json={"trade_id": 12345})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "llm_unavailable"
    assert body["result"] is None


# --------------------------------------------------------------------------- #
# claude-cli happy path — real trade, canned critic payload
# --------------------------------------------------------------------------- #

_CRITIC_PAYLOAD = {
    "verdict_by_phase": {
        "setup": "Level marked before entry.",
        "entry": "Entered on the reclaim.",
        "management": "Held per plan.",
        "exit": "Exited at target.",
    },
    "rule_checks": [
        {
            "rule": "Wait for the hourly level reclaim before entering.",
            "adherence": "pass",
            "source_citation_lesson_id": 1,
            "evidence_bar_ref": None,
            "note": "Entry aligned with the reclaim of the hourly level.",
        },
    ],
    "prescription": {"drill_id": None, "readings": []},
}


def test_status_available_under_claude_cli(client_cli):
    r = client_cli.get("/api/agents/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["llm"] == "claude-cli"


def test_critic_happy_path_persists_session(client_cli, monkeypatch):
    import app.llm.claude_cli as cli_mod
    monkeypatch.setattr(cli_mod, "_run_claude", _canned_run_fn(_CRITIC_PAYLOAD))
    _reset_binary_cache()

    trade_id = _seed_sim_trade(client_cli)

    r = client_cli.post("/api/agents/critic", json={"trade_id": trade_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["status"] == "ok"
    assert body["session_id"] is not None
    assert body["llm_backend"] == "claude-cli"

    # Result validates as a CriticResult.
    result = CriticResult.model_validate(body["result"])
    assert result.verdict_by_phase.setup == "Level marked before entry."
    assert len(result.rule_checks) == 1
    assert result.rule_checks[0].adherence == "pass"

    # Session + message persisted and retrievable (R4).
    got = client_cli.get(f"/api/agents/sessions/{body['session_id']}")
    assert got.status_code == 200
    session = got.json()
    assert session["kind"] == "critic"
    assert session["subject_ref"] == f"trade:{trade_id}"
    assert len(session["messages"]) == 1
    assert session["messages"][0]["role"] == "agent"
    # The structured payload rides on the persisted message.
    assert session["messages"][0]["payload"] is not None


def test_critic_unknown_trade_is_404(client_cli, monkeypatch):
    import app.llm.claude_cli as cli_mod
    monkeypatch.setattr(cli_mod, "_run_claude", _canned_run_fn(_CRITIC_PAYLOAD))
    _reset_binary_cache()

    # claude-cli forced → gets past the availability gate → 404 on the unknown id.
    r = client_cli.post("/api/agents/critic", json={"trade_id": 999999})
    assert r.status_code == 404
    assert r.json()["detail"] == "trade not found"


# --------------------------------------------------------------------------- #
# Never-500 degradation regressions (R1) — infra failures degrade, don't 500
# --------------------------------------------------------------------------- #

def _cli_client_no_raise(isolated_db, monkeypatch):
    """claude-cli-forced TestClient with raise_server_exceptions=False, so a
    would-be 500 surfaces as an HTTP 500 response rather than propagating out."""
    monkeypatch.setenv("TRADEDESK_LLM", "claude-cli")
    from app.config import get_settings

    get_settings.cache_clear()
    _reset_binary_cache()
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_knowledge_context_failure_degrades_not_500(isolated_db, monkeypatch):
    """A RuntimeError from build_knowledge_context (embedder model-load / corrupt index)
    on a VALID trade must degrade to 200 available=False status="llm_unavailable",
    NEVER a 500. The trade exists (past the 404 gate), so this exercises the
    context/critique try/except, not the not-found path."""
    client = _cli_client_no_raise(isolated_db, monkeypatch)
    monkeypatch.setattr(
        "app.llm.claude_cli._run_claude", _canned_run_fn(_CRITIC_PAYLOAD)
    )
    _reset_binary_cache()

    trade_id = _seed_sim_trade(client)

    # The route imports build_knowledge_context from app.llm.context at call time.
    with patch(
        "app.llm.context.build_knowledge_context", side_effect=RuntimeError("index corrupt")
    ):
        r = client.post("/api/agents/critic", json={"trade_id": trade_id})

    assert r.status_code == 200, f"expected degraded 200, got {r.status_code}"
    body = r.json()
    assert body["available"] is False
    assert body["status"] == "llm_unavailable"
    assert body["result"] is None


def test_store_failure_after_success_returns_result_null_session(isolated_db, monkeypatch):
    """A store failure AFTER a successful critique must NOT lose the critique to a
    500. The endpoint returns 200 available=True with the result present and
    session_id=None (best-effort persist)."""
    client = _cli_client_no_raise(isolated_db, monkeypatch)
    monkeypatch.setattr(
        "app.llm.claude_cli._run_claude", _canned_run_fn(_CRITIC_PAYLOAD)
    )
    _reset_binary_cache()

    trade_id = _seed_sim_trade(client)

    # The route imports create_session from app.db.agents_store at call time.
    with patch(
        "app.db.agents_store.create_session", side_effect=RuntimeError("db locked")
    ):
        r = client.post("/api/agents/critic", json={"trade_id": trade_id})

    assert r.status_code == 200, f"expected 200 (critique not lost), got {r.status_code}"
    body = r.json()
    assert body["available"] is True
    assert body["result"] is not None
    # Critique validates even though it could not be persisted.
    CriticResult.model_validate(body["result"])
    # Persist failed → no session id, but the result is not lost.
    assert body["session_id"] is None


# --------------------------------------------------------------------------- #
# UNIT — R3 citation drift guard (in-context resolved, out-of-context demoted)
# --------------------------------------------------------------------------- #

def test_out_of_context_citation_demoted_to_unverified():
    """The model cites two lessons: id 1 (IN the course context) and id 99 (NOT in
    context). The critic resolves id 1 to its KnowledgeSearchHit and demotes id 99 to
    None (unverified). Faithful lexical overlap is provided for the in-context
    citation so it survives the faithfulness heuristic."""
    _reset_binary_cache()

    # Course context holds exactly one lesson (id 1). Its text shares tokens with
    # the rule/note so the faithfulness heuristic keeps the citation.
    knowledge = KnowledgeContext(
        available=True,
        status="ok",
        query="support levels",
        hits=[_hit(1, text="Wait for the hourly level reclaim before entering.")],
        concepts=[],
    )
    trade_ctx = DebriefContext(trade=_trade())

    payload = {
        "verdict_by_phase": {"setup": "", "entry": "", "management": "", "exit": ""},
        "rule_checks": [
            {
                "rule": "Wait for the hourly level reclaim before entering.",
                "adherence": "pass",
                "source_citation_lesson_id": 1,   # IN context → resolved
                "note": "Entry aligned with the hourly level reclaim.",
            },
            {
                "rule": "Size down after two consecutive losers.",
                "adherence": "fail",
                "source_citation_lesson_id": 99,  # NOT in context → demoted
                "note": "Upsized after losing streak.",
            },
        ],
        "prescription": {"drill_id": None, "readings": []},
    }

    result = critique_trade(
        CriticInputs(trade_ctx=trade_ctx, knowledge=knowledge),
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(payload),
    )

    assert result is not None
    assert len(result.rule_checks) == 2

    in_ctx = result.rule_checks[0]
    out_ctx = result.rule_checks[1]

    # In-context citation resolved to the full hit.
    assert in_ctx.source_citation is not None
    assert in_ctx.source_citation.lesson_id == 1

    # Out-of-context citation demoted to unverified (None) — never fabricated.
    assert out_ctx.source_citation is None
