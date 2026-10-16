"""Tests for ClaudeCLIProvider.

All tests stub the subprocess runner — no real `claude` binary is invoked.
The runner is injected via the `_run` parameter on ClaudeCLIProvider, so
every test can control what subprocess.run would return or raise.

Mirrors the structure of test_llm_form.py.

data-eng task 6.2.1.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# Helpers shared across the module
# --------------------------------------------------------------------------- #

def _make_trade(
    *,
    symbol: str = "RIVN",
    direction: str = "long",
    avg_entry: float = 12.00,
    avg_exit: float | None = 12.50,
    stop_price: float | None = 11.80,
    net_pnl: float | None = 50.0,
    r_multiple: float | None = 2.5,
) -> "Trade":
    from app.models.trades import Direction, Trade, TradeEnrichment, TradeStatus
    return Trade(
        id=1,
        symbol=symbol,
        direction=Direction(direction),
        open_ts=datetime(2026, 6, 10, 13, 35, 0, tzinfo=UTC),
        close_ts=datetime(2026, 6, 10, 14, 10, 0, tzinfo=UTC),
        session_date=date(2026, 6, 10),
        qty=100.0,
        avg_entry=avg_entry,
        avg_exit=avg_exit,
        gross_pnl=net_pnl,
        fees=0.35,
        net_pnl=net_pnl,
        pnl_pct=0.042 if net_pnl else None,
        stop_price=stop_price,
        r_multiple=r_multiple,
        hold_time_s=2100,
        status=TradeStatus.closed,
        simulated=False,
        tags=[],
        enrichment=TradeEnrichment(),
    )


def _make_ctx(trade=None) -> "DebriefContext":
    from app.llm.base import DebriefContext
    t = trade or _make_trade()
    return DebriefContext(
        trade=t,
        fills=[],
        candles_around=[],
        enrichment=t.enrichment,
        tags=t.tags,
        checklist_template=[],
    )


def _make_reply(text: str, *, fields: dict | None = None) -> "DebriefReply":
    from app.models.training import DebriefReply
    return DebriefReply(text=text, fields=fields)


def _make_envelope(assistant_text: str, *, is_error: bool = False) -> str:
    """Build a minimal claude-cli JSON envelope string."""
    return json.dumps({
        "type": "result",
        "subtype": "success" if not is_error else "error_during_execution",
        "is_error": is_error,
        "api_error_status": None,
        "result": assistant_text,
    })


def _make_valid_turn_json(step: str = "setup") -> str:
    """Return a valid InterviewTurn JSON string."""
    return json.dumps({
        "step": step,
        "question": f"Walk me through the {step} of your RIVN trade at 12.00.",
        "options": None,
        "context_note": None,
        "done": False,
    })


def _successful_run(stdout: str, returncode: int = 0):
    """Return a factory that yields a CompletedProcess with given stdout."""
    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        return subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout=stdout,
            stderr="",
        )
    return _run


def _raising_run(exc):
    """Return a factory that raises *exc* on any call."""
    def _run(*args, **kwargs):
        raise exc
    return _run


def _make_provider(run_fn):
    """Construct a ClaudeCLIProvider with the given runner and a clean cache."""
    import app.llm.claude_cli as mod
    mod._BINARY_AVAILABLE = None  # reset per-test
    from app.llm.claude_cli import ClaudeCLIProvider
    return ClaudeCLIProvider(_run=run_fn)


def _version_ok_run(real_run_fn):
    """A runner that succeeds for --version and delegates everything else.

    Used for the 'binary present' scenario: the version check returns 0,
    then subsequent calls use real_run_fn.
    """
    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="1.0.0", stderr=""
            )
        return real_run_fn(args, input=input, capture_output=capture_output,
                           text=text, timeout=timeout)
    return _run


# --------------------------------------------------------------------------- #
# Test 1: binary absent → fallback to form backend
# --------------------------------------------------------------------------- #

class TestBinaryAbsentFallsBack:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_binary_absent_falls_back_to_form_start(self):
        """FileNotFoundError on any call → start_interview returns form-backend turn."""
        from app.llm.claude_cli import ClaudeCLIProvider

        provider = ClaudeCLIProvider(_run=_raising_run(FileNotFoundError("not found")))
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert turn.done is False
        assert turn.question is not None
        assert "RIVN" in turn.question


# --------------------------------------------------------------------------- #
# Test 2: non-zero exit code → fallback
# --------------------------------------------------------------------------- #

class TestNonZeroExitFallsBack:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_nonzero_exit_falls_back_to_form(self):
        """Exit code 1 (auth failure / rate limit) → fallback to form backend."""
        # Version check returns 0 so binary is seen as available; interview
        # call returns exit 1.
        call_count = [0]

        def _run(args, *, input="", capture_output=True, text=True, timeout=60):
            call_count[0] += 1
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="1.0.0", stderr=""
                )
            # Simulate auth failure
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="Authentication required",
            )

        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=_run)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert "RIVN" in turn.question


# --------------------------------------------------------------------------- #
# Test 3: valid JSON envelope → parsed to InterviewTurn
# --------------------------------------------------------------------------- #

class TestValidJsonResponseParsesToInterviewTurn:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_valid_json_response_parses_to_interview_turn(self):
        """Valid CLI envelope with a valid InterviewTurn in 'result' → returned as-is."""
        turn_json = _make_valid_turn_json("setup")
        envelope = _make_envelope(turn_json)

        run_fn = _version_ok_run(_successful_run(envelope))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert "RIVN" in turn.question
        assert turn.done is False

    def test_next_turn_valid_json_returns_plan_step(self):
        """next_turn with a valid plan-step envelope → returned directly."""
        turn_json = _make_valid_turn_json("plan")
        envelope = _make_envelope(turn_json)

        run_fn = _version_ok_run(_successful_run(envelope))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()
        transcript = [{"role": "agent", "step": "setup", "text": "Setup q?"}]
        reply = _make_reply("I saw a PDH break.")

        turn = provider.next_turn(ctx, transcript, reply)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.plan


# --------------------------------------------------------------------------- #
# Test 4: invalid JSON in result → fallback
# --------------------------------------------------------------------------- #

class TestInvalidJsonResponseFallsBack:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_invalid_json_in_result_falls_back(self):
        """Envelope whose 'result' is not valid JSON → fallback to form backend."""
        envelope = _make_envelope("not json {{{")

        run_fn = _version_ok_run(_successful_run(envelope))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert turn.question is not None

    def test_non_json_stdout_falls_back(self):
        """Completely non-JSON stdout (the outer envelope is broken) → fallback."""
        run_fn = _version_ok_run(_successful_run("Error: something went wrong"))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 5: shape mismatch (valid JSON but wrong fields) → fallback
# --------------------------------------------------------------------------- #

class TestShapeMismatchResponseFallsBack:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_missing_step_field_falls_back(self):
        """Valid JSON but missing 'step' field → Pydantic error → fallback."""
        bad_turn = json.dumps({"question": "What did you see?", "done": False})
        envelope = _make_envelope(bad_turn)

        run_fn = _version_ok_run(_successful_run(envelope))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        # Should fall back — FormProvider always produces a valid turn
        assert turn.question is not None

    def test_invalid_step_value_falls_back(self):
        """InterviewTurn with an unknown step enum value → fallback."""
        bad_turn = json.dumps({
            "step": "nonexistent_step",
            "question": "What?",
            "done": False,
        })
        envelope = _make_envelope(bad_turn)

        run_fn = _version_ok_run(_successful_run(envelope))
        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=run_fn)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 6: timeout → fallback
# --------------------------------------------------------------------------- #

class TestTimeoutFallsBack:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_timeout_falls_back_to_form(self):
        """subprocess.TimeoutExpired during interview call → fallback."""
        call_count = [0]

        def _run(args, *, input="", capture_output=True, text=True, timeout=60):
            call_count[0] += 1
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="1.0.0", stderr=""
                )
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=_run)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 7: binary check is cached (called only once across multiple turns)
# --------------------------------------------------------------------------- #

class TestBinaryCheckIsCached:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_binary_check_is_cached(self):
        """Multiple start_interview / next_turn calls result in ONE --version check."""
        version_check_count = [0]
        turn_json = _make_valid_turn_json("setup")
        envelope = _make_envelope(turn_json)

        def _run(args, *, input="", capture_output=True, text=True, timeout=60):
            if "--version" in args:
                version_check_count[0] += 1
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="1.0.0", stderr=""
                )
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=envelope, stderr=""
            )

        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=_run)
        ctx = _make_ctx()

        # Three calls — should only trigger one version check
        provider.start_interview(ctx)
        provider.start_interview(ctx)
        transcript = [{"role": "agent", "step": "setup", "text": "Setup q?"}]
        provider.next_turn(ctx, transcript, _make_reply("reply"))

        assert version_check_count[0] == 1, (
            f"Expected 1 version check, got {version_check_count[0]}"
        )


# --------------------------------------------------------------------------- #
# Test 8: secrets never appear in argv
# --------------------------------------------------------------------------- #

class TestSecretsNotInArgv:
    def setup_method(self):
        import app.llm.claude_cli as mod
        mod._BINARY_AVAILABLE = None

    def test_secrets_not_in_argv(self):
        """The subprocess args list must never contain token/key/secret substrings."""
        captured_args: list[list[str]] = []
        turn_json = _make_valid_turn_json("setup")
        envelope = _make_envelope(turn_json)

        def _run(args, *, input="", capture_output=True, text=True, timeout=60):
            captured_args.append(list(args))
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="1.0.0", stderr=""
                )
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=envelope, stderr=""
            )

        from app.llm.claude_cli import ClaudeCLIProvider
        provider = ClaudeCLIProvider(_run=_run)
        ctx = _make_ctx()
        provider.start_interview(ctx)

        _FORBIDDEN = ("token", "key", "secret", "password", "credential", "auth", "bearer")

        for args in captured_args:
            for arg in args:
                lower = arg.lower()
                for forbidden in _FORBIDDEN:
                    # "--system-prompt" is fine; "key" inside it is not a secret value.
                    # We check arg VALUES that look like secrets, not flag names.
                    # Flag names like "--allowedTools" are fine.
                    if arg.startswith("--"):
                        continue
                    assert forbidden not in lower, (
                        f"Potential secret substring {forbidden!r} found in argv: {arg!r}\n"
                        f"Full args: {args}"
                    )


# --------------------------------------------------------------------------- #
# Test 9: prompt includes trade facts
# --------------------------------------------------------------------------- #

class TestPromptIncludesTradeFacts:
    """Tests that build_prompt_start/build_prompt_next embed key trade fields."""

    def test_start_prompt_includes_symbol(self):
        from app.llm.claude_cli import build_prompt_start
        ctx = _make_ctx(_make_trade(symbol="HOOD"))
        system, user = build_prompt_start(ctx)
        assert "HOOD" in system

    def test_start_prompt_includes_avg_entry(self):
        from app.llm.claude_cli import build_prompt_start
        ctx = _make_ctx(_make_trade(avg_entry=15.42))
        system, user = build_prompt_start(ctx)
        assert "15.42" in system

    def test_start_prompt_includes_r_multiple(self):
        from app.llm.claude_cli import build_prompt_start
        ctx = _make_ctx(_make_trade(r_multiple=3.1))
        system, user = build_prompt_start(ctx)
        # r_multiple may appear as "3.10" depending on formatting
        assert "3.1" in system

    def test_start_prompt_includes_net_pnl(self):
        from app.llm.claude_cli import build_prompt_start
        ctx = _make_ctx(_make_trade(net_pnl=125.50))
        system, user = build_prompt_start(ctx)
        assert "125.50" in system

    def test_start_prompt_includes_tags(self):
        from app.llm.base import DebriefContext
        from app.models.trades import Tag
        trade = _make_trade()
        ctx = DebriefContext(
            trade=trade,
            fills=[],
            candles_around=[],
            enrichment=trade.enrichment,
            tags=[Tag(id=1, name="pdh-break"), Tag(id=2, name="vwap-reclaim")],
            checklist_template=[],
        )
        from app.llm.claude_cli import build_prompt_start
        system, user = build_prompt_start(ctx)
        assert "pdh-break" in system
        assert "vwap-reclaim" in system


# --------------------------------------------------------------------------- #
# Test 10: next_turn carries transcript
# --------------------------------------------------------------------------- #

class TestNextTurnCarriesTranscript:
    """Tests that build_prompt_next embeds transcript content in the user message."""

    def test_next_turn_prompt_includes_prior_user_reply(self):
        from app.llm.claude_cli import build_prompt_next
        ctx = _make_ctx()
        transcript = [
            {"role": "agent", "step": "setup", "text": "What did you see?"},
            {"role": "user", "step": "setup", "text": "I saw a PDH break above 12.18"},
        ]
        reply = _make_reply("I saw a PDH break above 12.18")
        _, user = build_prompt_next(ctx, transcript, reply)
        assert "PDH break" in user

    def test_next_turn_prompt_includes_reply_step(self):
        from app.llm.claude_cli import build_prompt_next
        ctx = _make_ctx()
        transcript = [
            {"role": "agent", "step": "setup", "text": "Setup question?"},
        ]
        reply = _make_reply("Confluence answer")
        _, user = build_prompt_next(ctx, transcript, reply)
        assert "setup" in user

    def test_next_turn_prompt_includes_all_prior_agent_steps(self):
        from app.llm.claude_cli import build_prompt_next
        ctx = _make_ctx()
        transcript = [
            {"role": "agent", "step": "setup", "text": "Setup q?"},
            {"role": "user", "step": "setup", "text": "I saw breakout"},
            {"role": "agent", "step": "plan", "text": "Plan q?"},
        ]
        reply = _make_reply("Stop at 11.80")
        _, user = build_prompt_next(ctx, transcript, reply)
        # Both prior turns should appear in the transcript section
        assert "Setup q?" in user
        assert "I saw breakout" in user

    def test_next_turn_reply_text_in_prompt(self):
        from app.llm.claude_cli import build_prompt_next
        ctx = _make_ctx()
        transcript = [{"role": "agent", "step": "feelings", "text": "How did you feel?"}]
        reply = _make_reply("I was calm and focused")
        _, user = build_prompt_next(ctx, transcript, reply)
        assert "calm and focused" in user


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #

def test_claude_cli_provider_satisfies_llm_provider_protocol():
    """ClaudeCLIProvider must satisfy the runtime_checkable LLMProvider Protocol."""
    import app.llm.claude_cli as mod
    mod._BINARY_AVAILABLE = None

    from app.llm.base import LLMProvider
    from app.llm.claude_cli import ClaudeCLIProvider

    provider = ClaudeCLIProvider()
    assert isinstance(provider, LLMProvider)


# --------------------------------------------------------------------------- #
# get_llm_provider factory integration
# --------------------------------------------------------------------------- #

def test_get_llm_provider_returns_claude_cli_for_setting(monkeypatch):
    """get_llm_provider(settings) returns ClaudeCLIProvider when llm='claude-cli'."""
    monkeypatch.setenv("TRADEDESK_LLM", "claude-cli")

    from app.config import get_settings, Settings
    get_settings.cache_clear()
    try:
        settings = Settings(llm="claude-cli")
        from app.llm import get_llm_provider
        provider = get_llm_provider(settings)
        from app.llm.claude_cli import ClaudeCLIProvider
        assert isinstance(provider, ClaudeCLIProvider)
    finally:
        get_settings.cache_clear()
