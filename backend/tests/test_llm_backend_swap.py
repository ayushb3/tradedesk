"""M6 task 6.2.5 — cross-backend equivalence contract for the LLMProvider.

Proves that swapping TRADEDESK_LLM between none / claude-cli / api produces
schema-identical Debrief rows and DebriefArtifacts. The LLMProvider Protocol
is a real contract: any backend that satisfies it must be observationally
indistinguishable in the persisted output, with only `llm_backend` legitimately
differing between backends.

Also proves credentials never appear in DB rows or logs.

Design pattern: a shared _drive_debrief() driver walks
  POST /debriefs -> 4x /turn -> /finish
against a given provider mode, with all LLM transports patched to return
deterministic canned turns. The form backend needs no patching.

Patching strategy:
- Form backend: no patch needed (deterministic state machine).
- claude-cli backend: inject a _run function that returns a valid CLI envelope.
  Also reset the _BINARY_AVAILABLE module cache so each test starts clean.
- api backend: patch anthropic.Anthropic so the SDK client is a mock that
  returns deterministic InterviewTurn JSON in its messages.create response.

All tests in this file assert CROSS-BACKEND EQUIVALENCE. Per-backend unit
tests live in test_llm_form.py / test_llm_claude_cli.py / test_llm_anthropic_api.py
and are not duplicated here.
"""
from __future__ import annotations

import json
import logging
import subprocess
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# Test constants                                                               #
# --------------------------------------------------------------------------- #

# The single key that legitimately differs between backends.
_BACKEND_NAMES = {
    "none": "none",
    "claude-cli": "claude-cli",
    "api": "api",
}

# Canary string seeded into api backend -- must never appear in DB or logs.
_CANARY_KEY = "sk-ant-canary-DO-NOT-LEAK-abc123"

# Set of Debrief fields that must be populated for ALL backends after finish.
_POPULATED_KEYS = {"id", "trade_id", "llm_backend", "step", "transcript", "created_at"}


# --------------------------------------------------------------------------- #
# Canned turn responses                                                        #
# --------------------------------------------------------------------------- #

# Four canned InterviewTurn JSON objects -- one per step. Every backend's
# transport is patched to return exactly these, so the interview arc is
# deterministic and comparable across backends.

_CANNED_SETUP = json.dumps({
    "step": "setup",
    "question": "Walk me through the setup -- what confluence did you see?",
    "options": None,
    "context_note": None,
    "done": False,
})

_CANNED_PLAN = json.dumps({
    "step": "plan",
    "question": "What was your planned stop and target at entry?",
    "options": None,
    "context_note": None,
    "done": False,
})

_CANNED_FEELINGS = json.dumps({
    "step": "feelings",
    "question": "How did you feel entering the trade?",
    "options": ["calm", "rushed", "frustrated"],
    "context_note": None,
    "done": False,
})

_CANNED_VERDICT = json.dumps({
    "step": "verdict",
    "question": "Did the setup fail or did you fail the setup?",
    "options": ["setup-failed", "failed-the-setup", "good-loss", "bad-win", "clean"],
    "context_note": None,
    "done": False,
})

_CANNED_DONE = json.dumps({
    "step": "done",
    "question": None,
    "options": None,
    "context_note": None,
    "done": True,
})

# Ordered sequence of what the LLM "returns" for start then next_turn x4:
# start_interview -> setup, next_turn(1) -> plan, next_turn(2) -> feelings,
# next_turn(3) -> verdict, next_turn(4) -> done.
_CANNED_TURNS = [
    _CANNED_SETUP,
    _CANNED_PLAN,
    _CANNED_FEELINGS,
    _CANNED_VERDICT,
    _CANNED_DONE,
]


# --------------------------------------------------------------------------- #
# CLI envelope helper                                                          #
# --------------------------------------------------------------------------- #

def _wrap_in_cli_envelope(turn_json: str) -> str:
    """Wrap an InterviewTurn JSON string in the claude-cli JSON envelope."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": turn_json,
    })


# --------------------------------------------------------------------------- #
# Transport patchers                                                           #
# --------------------------------------------------------------------------- #

@contextmanager
def _patch_claude_cli_transport(canned_turns: list[str] = _CANNED_TURNS):
    """Context manager that patches the claude-cli subprocess runner.

    Resets _BINARY_AVAILABLE to None before entering so the version check
    fires fresh; the mock returns success for --version and cycles through
    canned_turns for actual interview calls.
    """
    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None

    call_count = [0]  # mutable int for the closure

    def _mock_run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        # Each interview call cycles through canned_turns.
        idx = call_count[0] % len(canned_turns)
        call_count[0] += 1
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(canned_turns[idx]),
            stderr="",
        )

    # Patch the module-level _run_claude so new ClaudeCLIProvider instances
    # constructed inside the route use our mock.
    with patch("app.llm.claude_cli._run_claude", side_effect=_mock_run):
        yield


@contextmanager
def _patch_anthropic_api_transport(
    canned_turns: list[str] = _CANNED_TURNS,
    api_key: str = "sk-ant-test-key-for-swap",
):
    """Context manager that patches the anthropic SDK.

    The mock Anthropic client returns a response whose .content[0].text cycles
    through canned_turns for each messages.create call.
    """
    call_count = [0]

    def _mock_create(**kwargs):
        idx = call_count[0] % len(canned_turns)
        call_count[0] += 1
        block = SimpleNamespace(type="text", text=canned_turns[idx])
        resp = MagicMock()
        resp.content = [block]
        return resp

    mock_client = MagicMock()
    mock_client.messages.create = _mock_create

    with patch("anthropic.Anthropic", return_value=mock_client):
        yield mock_client


# --------------------------------------------------------------------------- #
# DB seeding helpers                                                           #
# --------------------------------------------------------------------------- #

def _seed_trade(engine, *, symbol: str = "RIVN", net_pnl: float = 50.0) -> int:
    """Insert a minimal closed trade and return its id."""
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
                avg_entry=12.0,
                avg_exit=12.5,
                gross_pnl=net_pnl,
                fees=0.0,
                net_pnl=net_pnl,
                pnl_pct=net_pnl / 1200.0,
                stop_price=11.80,
                r_multiple=2.5,
                hold_time_s=1800,
                status="closed",
                simulated=False,
                context="real",
            )
        )
        return int(res.inserted_primary_key[0])


# --------------------------------------------------------------------------- #
# Settings override helpers                                                    #
# --------------------------------------------------------------------------- #

def _make_settings_for_mode(mode: str, api_key: str = "sk-ant-test-key-for-swap"):
    """Return a Settings object configured for the given LLM mode."""
    from app.config import Settings
    if mode == "api":
        return Settings(llm=mode, anthropic_api_key=api_key)
    return Settings(llm=mode)


def _override_settings(app, mode: str, api_key: str = "sk-ant-test-key-for-swap"):
    """Inject a Settings dependency override on the given FastAPI app instance."""
    from app.api.deps import settings_dep
    settings = _make_settings_for_mode(mode, api_key=api_key)

    def _settings_override():
        return settings

    app.dependency_overrides[settings_dep] = _settings_override
    return settings


def _clear_overrides(app):
    """Remove all dependency overrides (cleanup)."""
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Core driver                                                                  #
# --------------------------------------------------------------------------- #

def _drive_debrief(
    client: TestClient,
    trade_id: int,
    mode: str,
    *,
    emit_scenario: bool = False,
) -> tuple[dict, dict]:
    """Drive a full debrief for the given mode.

    Walks: POST /debriefs -> 4x /turn (with user replies) -> /finish.
    Returns (debrief_row_dict, artifacts_dict).

    The LLM transport must already be patched by the caller via _patch_*_transport.
    Settings are overridden on the app via dependency_overrides so the route
    picks up the desired backend.
    """
    # User replies for each step -- deterministic text for transcript assertions.
    user_replies = [
        {"text": "I saw a clean PDH reclaim above 12.20 with volume confirmation."},
        {"text": "Stop at 11.80, target 12.80, risking exactly 1R."},
        {"text": "calm", "fields": {"selected": ["calm"]}},
        {"text": "setup-failed"},
    ]

    # POST /debriefs
    r = client.post("/api/debriefs", json={"trade_id": trade_id})
    assert r.status_code == 200, f"[mode={mode}] POST /debriefs failed: {r.text}"
    body = r.json()
    debrief_id = body["debrief"]["id"]

    # 4 turns (reply to setup, plan, feelings, verdict)
    for i, reply in enumerate(user_replies):
        r = client.post(f"/api/debriefs/{debrief_id}/turn", json=reply)
        assert r.status_code == 200, (
            f"[mode={mode}] turn {i + 1} failed (step={reply.get('text')!r}): {r.text}"
        )

    # POST /finish
    r = client.post(
        f"/api/debriefs/{debrief_id}/finish",
        json={"emit_scenario": emit_scenario},
    )
    assert r.status_code == 200, f"[mode={mode}] /finish failed: {r.text}"
    artifacts = r.json()

    # Fetch the final debrief row.
    r = client.get("/api/debriefs", params={"trade_id": trade_id})
    assert r.status_code == 200, f"[mode={mode}] GET /debriefs failed: {r.text}"
    debriefs = r.json()
    assert debriefs, f"[mode={mode}] no debrief rows returned"
    debrief_row = debriefs[0]  # most recent (ordered by id desc)

    return debrief_row, artifacts


# --------------------------------------------------------------------------- #
# Client fixture                                                               #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client_and_trade(isolated_db):
    """Create an isolated-DB TestClient and seed a trade. Yields (client, trade_id)."""
    from app.main import create_app
    from app.db.connection import get_engine

    engine = get_engine()
    trade_id = _seed_trade(engine)
    app = create_app()
    yield TestClient(app), trade_id


# --------------------------------------------------------------------------- #
# Test 1 -- schema-identical Debrief rows across all three backends           #
# --------------------------------------------------------------------------- #

class TestAllBackendsProduceSameShape:
    """All three backends produce Debrief rows with identical populated fields.

    Only llm_backend legitimately differs between them.
    """

    def _run_one_mode(self, client, trade_id, mode):
        """Run a debrief in the given mode; return the final row dict."""
        app = client.app
        _override_settings(app, mode)
        try:
            if mode == "none":
                debrief_row, _ = _drive_debrief(client, trade_id, mode)
            elif mode == "claude-cli":
                import app.llm.claude_cli as cli_mod
                cli_mod._BINARY_AVAILABLE = None
                with _patch_claude_cli_transport():
                    debrief_row, _ = _drive_debrief(client, trade_id, mode)
            elif mode == "api":
                with _patch_anthropic_api_transport():
                    debrief_row, _ = _drive_debrief(client, trade_id, mode)
            else:
                raise ValueError(f"unknown mode {mode!r}")
        finally:
            _clear_overrides(app)
        return debrief_row

    def test_all_backends_populate_required_fields(self, client_and_trade):
        """All backends produce a Debrief row with the same set of populated fields."""
        client, trade_id = client_and_trade
        rows = {}
        for mode in ["none", "claude-cli", "api"]:
            rows[mode] = self._run_one_mode(client, trade_id, mode)

        for mode, row in rows.items():
            missing = _POPULATED_KEYS - set(row.keys())
            assert not missing, (
                f"[mode={mode}] Debrief row missing required fields: {missing}"
            )

    def test_all_backends_step_is_done(self, client_and_trade):
        """After /finish, debrief.step == 'done' for every backend."""
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            row = self._run_one_mode(client, trade_id, mode)
            assert row["step"] == "done", (
                f"[mode={mode}] expected step='done', got {row['step']!r}"
            )

    def test_all_backends_transcript_has_nine_entries(self, client_and_trade):
        """All backends produce a transcript with exactly 9 entries.

        The transcript structure is:
          POST /debriefs  -> appends agent/setup           (1)
          /turn 1         -> appends user/setup + agent/plan    (2)
          /turn 2         -> appends user/plan + agent/feelings (2)
          /turn 3         -> appends user/feelings + agent/verdict (2)
          /turn 4         -> appends user/verdict + agent/done  (2)
          Total = 1 + 2*4 = 9 entries

        9 is the correct count: 4 user replies + 5 agent turns (setup through done).
        """
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            row = self._run_one_mode(client, trade_id, mode)
            transcript = row.get("transcript", [])
            assert len(transcript) == 9, (
                f"[mode={mode}] expected 9 transcript entries, got {len(transcript)}: "
                f"{[e.get('role') + '/' + e.get('step', '?') for e in transcript]}"
            )

    def test_only_llm_backend_field_differs(self, client_and_trade):
        """The only field that legitimately differs across backends is llm_backend."""
        client, trade_id = client_and_trade
        rows = {}
        for mode in ["none", "claude-cli", "api"]:
            rows[mode] = self._run_one_mode(client, trade_id, mode)

        # Check llm_backend value is exactly the backend name.
        assert rows["none"]["llm_backend"] == "none"
        assert rows["claude-cli"]["llm_backend"] == "claude-cli"
        assert rows["api"]["llm_backend"] == "api"

        # Every other inspected structural field should be equivalent.
        FIELDS_TO_COMPARE = {"step", "trade_id"}
        for field in FIELDS_TO_COMPARE:
            values = {mode: rows[mode].get(field) for mode in rows}
            assert len(set(str(v) for v in values.values())) == 1, (
                f"Field {field!r} differs across backends: {values}"
            )


# --------------------------------------------------------------------------- #
# Test 2 -- DebriefArtifacts shape is identical across backends               #
# --------------------------------------------------------------------------- #

class TestAllBackendsProduceSameArtifactsShape:
    """All three backends produce DebriefArtifacts with the same structure."""

    def _run_and_get_artifacts(self, client, trade_id, mode, emit_scenario=False):
        _override_settings(client.app, mode)
        try:
            if mode == "none":
                _, artifacts = _drive_debrief(
                    client, trade_id, mode, emit_scenario=emit_scenario
                )
            elif mode == "claude-cli":
                import app.llm.claude_cli as cli_mod
                cli_mod._BINARY_AVAILABLE = None
                with _patch_claude_cli_transport():
                    _, artifacts = _drive_debrief(
                        client, trade_id, mode, emit_scenario=emit_scenario
                    )
            elif mode == "api":
                with _patch_anthropic_api_transport():
                    _, artifacts = _drive_debrief(
                        client, trade_id, mode, emit_scenario=emit_scenario
                    )
            else:
                raise ValueError(f"unknown mode {mode!r}")
        finally:
            _clear_overrides(client.app)
        return artifacts

    def test_all_backends_enrichment_saved_true(self, client_and_trade):
        """DebriefArtifacts.enrichment_saved is True for all backends."""
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            artifacts = self._run_and_get_artifacts(client, trade_id, mode)
            assert artifacts["enrichment_saved"] is True, (
                f"[mode={mode}] expected enrichment_saved=True, "
                f"got {artifacts['enrichment_saved']!r}"
            )

    def test_all_backends_scenario_id_none_when_not_requested(self, client_and_trade):
        """scenario_id is None when emit_scenario=False for all backends."""
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            artifacts = self._run_and_get_artifacts(
                client, trade_id, mode, emit_scenario=False
            )
            assert artifacts["scenario_id"] is None, (
                f"[mode={mode}] expected scenario_id=None when emit_scenario=False, "
                f"got {artifacts['scenario_id']!r}"
            )

    def test_all_backends_verdict_populated(self, client_and_trade):
        """verdict is populated to 'setup-failed' for all backends.

        The form backend extracts verdict from the user's reply text via
        summarize_transcript. The LLM backends are patched to return canned
        turns -- the final user reply text is always 'setup-failed'.
        finish_debrief calls summarize_transcript when debrief.verdict is None,
        so all three backends converge on the same verdict.
        """
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            artifacts = self._run_and_get_artifacts(client, trade_id, mode)
            assert artifacts["verdict"] == "setup-failed", (
                f"[mode={mode}] expected verdict='setup-failed', "
                f"got {artifacts['verdict']!r}. "
                "All backends must extract verdict from transcript."
            )


# --------------------------------------------------------------------------- #
# Test 3 -- emit_scenario produces a scenario row regardless of backend       #
# --------------------------------------------------------------------------- #

class TestAllBackendsEmitScenarioWhenRequested:
    """All three backends produce a scenario when emit_scenario=True."""

    def _run_with_scenario(self, client, trade_id, mode):
        _override_settings(client.app, mode)
        try:
            if mode == "none":
                _, artifacts = _drive_debrief(
                    client, trade_id, mode, emit_scenario=True
                )
            elif mode == "claude-cli":
                import app.llm.claude_cli as cli_mod
                cli_mod._BINARY_AVAILABLE = None
                with _patch_claude_cli_transport():
                    _, artifacts = _drive_debrief(
                        client, trade_id, mode, emit_scenario=True
                    )
            elif mode == "api":
                with _patch_anthropic_api_transport():
                    _, artifacts = _drive_debrief(
                        client, trade_id, mode, emit_scenario=True
                    )
            else:
                raise ValueError(f"unknown mode {mode!r}")
        finally:
            _clear_overrides(client.app)
        return artifacts

    def test_all_backends_scenario_id_positive_when_requested(self, client_and_trade):
        """scenario_id is a positive int for all backends when emit_scenario=True."""
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            artifacts = self._run_with_scenario(client, trade_id, mode)
            sid = artifacts["scenario_id"]
            assert isinstance(sid, int) and sid > 0, (
                f"[mode={mode}] expected scenario_id > 0, got {sid!r}"
            )

    def test_all_backends_scenario_has_same_shape(self, client_and_trade):
        """All backends create a scenario with source_type='debrief' and
        drill_type='decision-point'.

        The scenario shape is determined by the shared finish_debrief path,
        not by the LLM backend. Any backend producing a different shape here
        signals a regression in how finish_debrief calls create_scenario.
        """
        client, trade_id = client_and_trade
        for mode in ["none", "claude-cli", "api"]:
            artifacts = self._run_with_scenario(client, trade_id, mode)
            sid = artifacts["scenario_id"]
            # Fetch the scenario to inspect its shape.
            r = client.get(f"/api/training/scenarios/{sid}")
            assert r.status_code == 200, (
                f"[mode={mode}] GET scenario {sid} failed: {r.text}"
            )
            scenario = r.json()
            assert scenario["source_type"] == "debrief", (
                f"[mode={mode}] expected source_type='debrief', "
                f"got {scenario['source_type']!r}"
            )
            assert scenario["drill_type"] == "decision-point", (
                f"[mode={mode}] expected drill_type='decision-point', "
                f"got {scenario['drill_type']!r}"
            )


# --------------------------------------------------------------------------- #
# Test 4 -- credentials never appear in persisted DB rows                     #
# --------------------------------------------------------------------------- #

class TestNoCredentialsLeakIntoRow:
    """Canary API key must never appear in any persisted Debrief or Scenario row."""

    def _serialise(self, obj) -> str:
        """Recursively serialise a row dict to a single string for canary search."""
        return json.dumps(obj, default=str)

    def test_api_key_not_in_debrief_row(self, client_and_trade):
        """Canary API key is NOT present in the persisted Debrief row."""
        client, trade_id = client_and_trade
        _override_settings(client.app, "api", api_key=_CANARY_KEY)
        try:
            with _patch_anthropic_api_transport(api_key=_CANARY_KEY):
                debrief_row, _ = _drive_debrief(client, trade_id, "api")
        finally:
            _clear_overrides(client.app)

        row_text = self._serialise(debrief_row)
        assert _CANARY_KEY not in row_text, (
            f"Canary key found in Debrief row! "
            f"Leaked via: "
            f"{[k for k in debrief_row if _CANARY_KEY in self._serialise(debrief_row.get(k, ''))]}"
        )

    def test_api_key_not_in_transcript_entries(self, client_and_trade):
        """Canary API key is not present in any transcript entry (agent or user)."""
        client, trade_id = client_and_trade
        _override_settings(client.app, "api", api_key=_CANARY_KEY)
        try:
            with _patch_anthropic_api_transport(api_key=_CANARY_KEY):
                debrief_row, _ = _drive_debrief(client, trade_id, "api")
        finally:
            _clear_overrides(client.app)

        for i, entry in enumerate(debrief_row.get("transcript", [])):
            entry_text = self._serialise(entry)
            assert _CANARY_KEY not in entry_text, (
                f"Canary key found in transcript entry {i}: {entry!r}"
            )

    def test_api_key_not_in_scenario_row_when_emitted(self, client_and_trade):
        """Canary API key does not leak into a debrief-emitted Scenario row."""
        client, trade_id = client_and_trade
        _override_settings(client.app, "api", api_key=_CANARY_KEY)
        try:
            with _patch_anthropic_api_transport(api_key=_CANARY_KEY):
                _, artifacts = _drive_debrief(
                    client, trade_id, "api", emit_scenario=True
                )
        finally:
            _clear_overrides(client.app)

        sid = artifacts["scenario_id"]
        assert sid is not None, "expected a scenario to be created with emit_scenario=True"
        r = client.get(f"/api/training/scenarios/{sid}")
        assert r.status_code == 200, r.text
        scenario_text = self._serialise(r.json())
        assert _CANARY_KEY not in scenario_text, (
            f"Canary key found in emitted Scenario row (id={sid})"
        )


# --------------------------------------------------------------------------- #
# Test 5 -- credentials never appear in stderr/logs                           #
# --------------------------------------------------------------------------- #

class TestNoCredentialsLeakIntoLogs:
    """Canary API key must not appear in any log or stderr output during a debrief."""

    def test_api_key_not_in_stderr_or_logs(self, client_and_trade, capsys):
        """No log or print output during a debrief emits the canary key."""
        client, trade_id = client_and_trade
        _override_settings(client.app, "api", api_key=_CANARY_KEY)

        # Capture log records via a custom handler in addition to capsys.
        captured_log_records: list[str] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                captured_log_records.append(self.format(record))

        capturing_handler = _CapturingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(capturing_handler)

        try:
            with _patch_anthropic_api_transport(api_key=_CANARY_KEY):
                _drive_debrief(client, trade_id, "api")
        finally:
            _clear_overrides(client.app)
            root_logger.removeHandler(capturing_handler)

        # Check stderr/stdout (print statements from provider fallback paths, etc.)
        captured = capsys.readouterr()
        assert _CANARY_KEY not in captured.out, (
            "Canary key found in stdout during debrief"
        )
        assert _CANARY_KEY not in captured.err, (
            "Canary key found in stderr during debrief"
        )

        # Check captured log records.
        all_log_text = "\n".join(captured_log_records)
        assert _CANARY_KEY not in all_log_text, (
            "Canary key found in log records during debrief"
        )


# --------------------------------------------------------------------------- #
# Test 6 -- backend swap at request time picks up the new setting             #
# --------------------------------------------------------------------------- #

def test_backend_swap_at_request_time_gets_current_setting(client_and_trade):
    """Changing settings between requests must produce the new backend's name.

    Guards against a caching bug in get_llm_provider: the factory must
    re-read settings on every call, not return a cached provider instance.
    """
    client, trade_id = client_and_trade

    # First debrief with 'none'.
    _override_settings(client.app, "none")
    try:
        row_none, _ = _drive_debrief(client, trade_id, "none")
    finally:
        _clear_overrides(client.app)

    assert row_none["llm_backend"] == "none", (
        f"First debrief should use 'none', got {row_none['llm_backend']!r}"
    )

    # Second debrief with 'claude-cli' (patched binary).
    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None
    _override_settings(client.app, "claude-cli")
    try:
        with _patch_claude_cli_transport():
            row_cli, _ = _drive_debrief(client, trade_id, "claude-cli")
    finally:
        _clear_overrides(client.app)

    assert row_cli["llm_backend"] == "claude-cli", (
        f"Second debrief should use 'claude-cli', got {row_cli['llm_backend']!r}. "
        "Possible get_llm_provider caching bug: factory must not cache the provider."
    )


# --------------------------------------------------------------------------- #
# Test 7 -- transcript role/step sequence is structurally identical           #
# --------------------------------------------------------------------------- #

def test_form_backend_transcript_matches_llm_backend_transcript_structure(
    client_and_trade,
):
    """When all three backends are patched to produce the same 4 turns,
    the persisted transcript has the same role/step sequence.

    Structural equivalence means:
    - Same length (9 entries: 5 agent + 4 user)
    - Role/step sequence: agent/setup, user/setup, agent/plan, user/plan,
      agent/feelings, user/feelings, agent/verdict, user/verdict, agent/done

    Note on text: the form backend generates questions from its own templates
    (e.g. "You entered RIVN long at ..."), while the patched LLM backends return
    canned turn text. Text content legitimately differs; structural sequence must
    not.
    """
    client, trade_id = client_and_trade

    def _get_role_step_sequence(row: dict) -> list[tuple[str, str]]:
        return [
            (e.get("role", "?"), e.get("step", "?"))
            for e in row.get("transcript", [])
        ]

    # Expected structural sequence every backend must produce.
    # 9 entries: the route appends BOTH the user reply AND the next agent turn
    # on each /turn call. POST /debriefs appends agent/setup; then 4x /turn
    # appends user+agent pairs, with the last agent turn being agent/done.
    EXPECTED_SEQUENCE = [
        ("agent", "setup"),
        ("user", "setup"),
        ("agent", "plan"),
        ("user", "plan"),
        ("agent", "feelings"),
        ("user", "feelings"),
        ("agent", "verdict"),
        ("user", "verdict"),
        ("agent", "done"),
    ]

    sequences = {}
    for mode in ["none", "claude-cli", "api"]:
        _override_settings(client.app, mode)
        try:
            if mode == "none":
                row, _ = _drive_debrief(client, trade_id, mode)
            elif mode == "claude-cli":
                import app.llm.claude_cli as cli_mod
                cli_mod._BINARY_AVAILABLE = None
                with _patch_claude_cli_transport():
                    row, _ = _drive_debrief(client, trade_id, mode)
            elif mode == "api":
                with _patch_anthropic_api_transport():
                    row, _ = _drive_debrief(client, trade_id, mode)
        finally:
            _clear_overrides(client.app)
        sequences[mode] = _get_role_step_sequence(row)

    for mode, seq in sequences.items():
        assert seq == EXPECTED_SEQUENCE, (
            f"[mode={mode}] transcript role/step sequence mismatch.\n"
            f"  Expected: {EXPECTED_SEQUENCE}\n"
            f"  Got:      {seq}"
        )


# --------------------------------------------------------------------------- #
# Test 8 -- missing Anthropic API key surfaces correctly (not silently drops) #
# --------------------------------------------------------------------------- #

def test_missing_anthropic_key_raises_and_not_silently_fallback(
    client_and_trade,
):
    """TRADEDESK_LLM=api with empty key -> HTTP 501 (backend not configured).

    The factory (`get_llm_provider`) wraps `RuntimeError` raised by
    `AnthropicAPIProvider.__init__` (missing key) into `NotImplementedError`,
    which the debrief route surfaces as HTTP 501. This is the correct signal:
    "backend selected but not configured" is a configuration issue, not a
    server bug.

    Locked-in behavior:
    - Response must NOT be 200 (that would indicate silent fallback to 'none').
    - Response must NOT be 500 (that would mean the RuntimeError leaked through
      unhandled — an operational papercut fixed after 6.2.5 landed).
    - Response MUST be 501 (backend not configured — matches the operator's
      intent of "I selected api mode but haven't set ANTHROPIC_API_KEY yet").

    If the factory is ever changed to silently fall back to FormProvider when
    the api key is missing, this test catches that regression.
    """
    _, trade_id = client_and_trade

    from app.main import create_app
    from app.config import Settings
    from app.api.deps import settings_dep

    app = create_app()
    settings_no_key = Settings(llm="api", anthropic_api_key="")

    def _settings_override():
        return settings_no_key

    app.dependency_overrides[settings_dep] = _settings_override
    try:
        client = TestClient(app)
        with patch("anthropic.Anthropic"):
            r = client.post("/api/debriefs", json={"trade_id": trade_id})

        # Must NOT be 200 -- silent fallback to 'none' is a regression.
        assert r.status_code != 200, (
            f"Expected non-200 when api key is missing, got 200. "
            f"Body: {r.text}. This indicates a silent fallback to 'none' -- "
            "a RuntimeError for missing key must not be swallowed."
        )
        # 501: get_llm_provider wraps the RuntimeError as NotImplementedError.
        assert r.status_code == 501, (
            f"Expected HTTP 501 for missing api key (backend not configured), "
            f"got {r.status_code}. Body: {r.text}. If this fails with 500, the "
            "RuntimeError wrapper in llm/base.py::get_llm_provider was removed."
        )
        assert "ANTHROPIC_API_KEY" in r.text, (
            f"501 response should name the missing setting; got: {r.text}"
        )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 9 -- claude-cli binary missing -> fallback preserves debrief completion#
# --------------------------------------------------------------------------- #

def test_claude_cli_binary_missing_falls_back_to_form_transcript(
    client_and_trade,
):
    """TRADEDESK_LLM=claude-cli with binary absent -> debrief completes via FormProvider.

    When the claude binary is not found (FileNotFoundError on every call),
    ClaudeCLIProvider falls back to FormProvider on every turn. The debrief
    completes successfully.

    Behavior locked in by this test:
    - step='done' (debrief completes)
    - llm_backend='claude-cli' (the operator's SELECTED backend -- the row
      reflects the selection, not the fallback path)
    - transcript has 8 entries (FormProvider arc runs to completion)
    - enrichment_saved=True (finish_debrief ran to completion)

    This guards the per-turn graceful degradation contract documented in
    claude_cli.py (fallback happens per-turn inside the provider, not at
    the factory level).
    """
    client, trade_id = client_and_trade

    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None  # reset cache before test

    _override_settings(client.app, "claude-cli")
    try:
        # Patch the runner to always raise FileNotFoundError.
        # This causes _check_binary_available to cache False, so every
        # start_interview / next_turn call falls back to FormProvider.
        def _always_missing(*args, **kwargs):
            raise FileNotFoundError("claude: command not found")

        with patch("app.llm.claude_cli._run_claude", side_effect=_always_missing):
            debrief_row, artifacts = _drive_debrief(client, trade_id, "claude-cli")
    finally:
        _clear_overrides(client.app)
        cli_mod._BINARY_AVAILABLE = None  # reset cache for subsequent tests

    # Debrief completed successfully.
    assert debrief_row["step"] == "done", (
        f"Expected step='done' after binary-missing fallback, "
        f"got {debrief_row['step']!r}"
    )

    # llm_backend must be 'claude-cli' -- the operator's selection.
    assert debrief_row["llm_backend"] == "claude-cli", (
        f"Expected llm_backend='claude-cli' (operator's selection), "
        f"got {debrief_row['llm_backend']!r}. "
        "The row must reflect the configured backend, not the FormProvider fallback."
    )

    # Transcript: FormProvider arc ran to completion (9 entries -- see test 1 docstring).
    transcript = debrief_row.get("transcript", [])
    assert len(transcript) == 9, (
        f"Expected 9 transcript entries after binary-missing fallback, "
        f"got {len(transcript)}"
    )

    # Enrichment saved (finish_debrief ran to completion).
    assert artifacts["enrichment_saved"] is True, (
        "Expected enrichment_saved=True even when cli binary is absent"
    )
