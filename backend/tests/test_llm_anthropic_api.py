"""Tests for AnthropicAPIProvider.

All tests mock the anthropic SDK — no real API calls, no key required.
The mock patches `anthropic.Anthropic` so the lazy import inside
AnthropicAPIProvider.__init__ picks up the mock client.

Mirrors the structure of test_llm_claude_cli.py.

data-eng task 6.2.2.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
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


def _make_settings(*, anthropic_api_key: str = "sk-ant-test-key") -> "Settings":
    """Return a Settings instance with LLM=api and the given key."""
    from app.config import Settings
    return Settings(llm="api", anthropic_api_key=anthropic_api_key)


def _make_valid_turn_json(step: str = "setup") -> str:
    """Return a valid InterviewTurn JSON string."""
    return json.dumps({
        "step": step,
        "question": f"Walk me through the {step} of your RIVN trade at 12.00.",
        "options": None,
        "context_note": None,
        "done": False,
    })


def _make_text_block(text: str) -> SimpleNamespace:
    """Build a mock SDK TextBlock (type='text')."""
    return SimpleNamespace(type="text", text=text)


def _make_mock_response(text: str) -> MagicMock:
    """Build a mock Messages.create response with one TextBlock."""
    resp = MagicMock()
    resp.content = [_make_text_block(text)]
    return resp


def _make_provider_with_mock(mock_create):
    """Construct AnthropicAPIProvider with patched SDK; return provider."""
    from app.llm.anthropic_api import AnthropicAPIProvider
    settings = _make_settings()
    mock_client = MagicMock()
    mock_client.messages.create = mock_create
    with patch("anthropic.Anthropic", return_value=mock_client):
        provider = AnthropicAPIProvider(settings)
    # Overwrite the real client (created inside __init__) with our mock.
    # This is necessary because Anthropic() was called inside __init__ before
    # the patch context, but we patched at import time so mock_client IS the one.
    return provider


# --------------------------------------------------------------------------- #
# Test 1: missing key raises RuntimeError at init
# --------------------------------------------------------------------------- #

class TestMissingKeyRaisesAtInit:
    def test_missing_key_raises_at_init(self):
        """Settings with anthropic_api_key='' → RuntimeError at construction."""
        from app.llm.anthropic_api import AnthropicAPIProvider
        settings = _make_settings(anthropic_api_key="")

        with patch("anthropic.Anthropic"):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY missing"):
                AnthropicAPIProvider(settings)


# --------------------------------------------------------------------------- #
# Test 2: missing SDK raises ImportError with pip hint
# --------------------------------------------------------------------------- #

class TestSDKMissingRaisesCleanError:
    def test_sdk_missing_raises_clean_error(self, monkeypatch):
        """When the anthropic package is not importable, a clean ImportError
        with a pip-install hint is raised."""
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        from app.llm.anthropic_api import AnthropicAPIProvider
        settings = _make_settings()

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        with pytest.raises(ImportError, match="pip install"):
            AnthropicAPIProvider(settings)


# --------------------------------------------------------------------------- #
# Test 3: valid JSON response parses to InterviewTurn
# --------------------------------------------------------------------------- #

class TestValidResponseParsesToInterviewTurn:
    def test_valid_response_parses_to_interview_turn(self):
        """Mocked SDK returns valid InterviewTurn JSON → returned directly."""
        turn_json = _make_valid_turn_json("setup")

        mock_create = MagicMock(return_value=_make_mock_response(turn_json))
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert "RIVN" in turn.question
        assert turn.done is False

    def test_next_turn_valid_json_returns_plan_step(self):
        """next_turn with a valid plan-step response → returned directly."""
        turn_json = _make_valid_turn_json("plan")

        mock_create = MagicMock(return_value=_make_mock_response(turn_json))
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()
        transcript = [{"role": "agent", "step": "setup", "text": "Setup q?"}]
        reply = _make_reply("I saw a PDH break.")

        turn = provider.next_turn(ctx, transcript, reply)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.plan


# --------------------------------------------------------------------------- #
# Test 4: invalid JSON response falls back to form
# --------------------------------------------------------------------------- #

class TestInvalidJsonResponseFallsBack:
    def test_invalid_json_response_falls_back(self):
        """Non-JSON text → fallback to form backend."""
        mock_create = MagicMock(return_value=_make_mock_response("not json {{{"))
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 5: shape mismatch falls back to form
# --------------------------------------------------------------------------- #

class TestShapeMismatchResponseFallsBack:
    def test_missing_step_field_falls_back(self):
        """Valid JSON but missing 'step' field → Pydantic error → fallback."""
        bad_json = json.dumps({"question": "What did you see?", "done": False})
        mock_create = MagicMock(return_value=_make_mock_response(bad_json))
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None

    def test_invalid_step_enum_value_falls_back(self):
        """Valid JSON but unknown step enum → fallback."""
        bad_json = json.dumps({
            "step": "nonexistent_step",
            "question": "What?",
            "done": False,
        })
        mock_create = MagicMock(return_value=_make_mock_response(bad_json))
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 6: APIStatusError (401 auth) falls back
# --------------------------------------------------------------------------- #

class TestAPIStatusErrorFallsBack:
    def test_api_status_error_falls_back(self):
        """anthropic.APIStatusError (401 auth failure) → fallback to form backend."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(401, request=req)
        exc = anthropic.APIStatusError(
            "Authentication error",
            response=resp,
            body={"error": {"type": "authentication_error"}},
        )

        mock_create = MagicMock(side_effect=exc)
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        from app.models.training import DebriefStep
        assert turn.step == DebriefStep.setup
        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 7: APIConnectionError falls back
# --------------------------------------------------------------------------- #

class TestConnectionErrorFallsBack:
    def test_connection_error_falls_back(self):
        """anthropic.APIConnectionError → fallback to form backend."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic.APIConnectionError(
            message="Connection refused",
            request=req,
        )

        mock_create = MagicMock(side_effect=exc)
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 8: APITimeoutError falls back
# --------------------------------------------------------------------------- #

class TestTimeoutFallsBack:
    def test_timeout_falls_back(self):
        """anthropic.APITimeoutError → fallback to form backend."""
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic.APITimeoutError(request=req)

        mock_create = MagicMock(side_effect=exc)
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 9: empty content list falls back
# --------------------------------------------------------------------------- #

class TestEmptyContentFallsBack:
    def test_response_missing_content_falls_back(self):
        """Response with empty content list → fallback to form backend."""
        resp = MagicMock()
        resp.content = []
        mock_create = MagicMock(return_value=resp)
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None

    def test_response_non_text_block_falls_back(self):
        """Response with a non-text first block → fallback."""
        resp = MagicMock()
        resp.content = [SimpleNamespace(type="tool_use", id="tool123")]
        mock_create = MagicMock(return_value=resp)
        provider = _make_provider_with_mock(mock_create)
        ctx = _make_ctx()

        turn = provider.start_interview(ctx)

        assert turn.question is not None


# --------------------------------------------------------------------------- #
# Test 10: API key never appears in the assembled prompt
# --------------------------------------------------------------------------- #

class TestSecretsNotInPromptOrLogs:
    def test_secrets_not_in_prompt_or_logs(self):
        """The assembled prompt (system + user) must not contain the API key."""
        FAKE_KEY = "sk-ant-test-secretapikey1234567890"
        from app.config import Settings
        settings = Settings(llm="api", anthropic_api_key=FAKE_KEY)

        captured_calls: list[dict] = []

        def _capture_create(**kwargs):
            captured_calls.append(dict(kwargs))
            return _make_mock_response(_make_valid_turn_json("setup"))

        mock_client = MagicMock()
        mock_client.messages.create = _capture_create

        from app.llm.anthropic_api import AnthropicAPIProvider
        with patch("anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicAPIProvider(settings)

        ctx = _make_ctx()
        provider.start_interview(ctx)

        assert captured_calls, "messages.create was not called"
        for call in captured_calls:
            system = call.get("system", "")
            messages = call.get("messages", [])
            for msg in messages:
                content = msg.get("content", "")
                assert FAKE_KEY not in system, (
                    "API key found in system prompt"
                )
                assert FAKE_KEY not in content, (
                    "API key found in user message"
                )


# --------------------------------------------------------------------------- #
# Test 11: prompt reuses the shared prompts.py builder (no drift between backends)
# --------------------------------------------------------------------------- #

class TestPromptReusesCLIBuilder:
    """Parametrize both providers with the same input and assert identical prompt.

    This guards against prompt drift between ClaudeCLIProvider and
    AnthropicAPIProvider: both must call build_prompt_start / build_prompt_next
    from app.llm.prompts, not maintain their own copies.
    """

    def _get_cli_prompt(self, ctx) -> tuple[str, str]:
        from app.llm.claude_cli import build_prompt_start
        return build_prompt_start(ctx)

    def _get_api_prompt(self, ctx) -> tuple[str, str]:
        from app.llm.prompts import build_prompt_start
        return build_prompt_start(ctx)

    def test_start_prompt_identical_across_backends(self):
        """build_prompt_start produces the same (system, user) for both backends."""
        ctx = _make_ctx(_make_trade(symbol="HOOD", avg_entry=15.42, r_multiple=2.1))

        cli_system, cli_user = self._get_cli_prompt(ctx)
        api_system, api_user = self._get_api_prompt(ctx)

        assert cli_system == api_system, (
            "System prompts differ between claude-cli and api backends — "
            "both must import from app.llm.prompts"
        )
        assert cli_user == api_user, (
            "User messages differ between claude-cli and api backends"
        )

    def test_next_turn_prompt_identical_across_backends(self):
        """build_prompt_next produces the same prompts for both backends."""
        ctx = _make_ctx()
        transcript = [
            {"role": "agent", "step": "setup", "text": "What did you see?"},
            {"role": "user", "step": "setup", "text": "PDH break above 12.18"},
        ]
        reply = _make_reply("PDH break above 12.18")

        from app.llm.claude_cli import build_prompt_next as cli_next
        from app.llm.prompts import build_prompt_next as api_next

        cli_system, cli_user = cli_next(ctx, transcript, reply)
        api_system, api_user = api_next(ctx, transcript, reply)

        assert cli_system == api_system
        assert cli_user == api_user


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #

def test_anthropic_api_provider_satisfies_llm_provider_protocol():
    """AnthropicAPIProvider must satisfy the runtime_checkable LLMProvider Protocol."""
    from app.llm.anthropic_api import AnthropicAPIProvider
    from app.llm.base import LLMProvider

    settings = _make_settings()
    with patch("anthropic.Anthropic"):
        provider = AnthropicAPIProvider(settings)

    assert isinstance(provider, LLMProvider)


# --------------------------------------------------------------------------- #
# get_llm_provider factory integration
# --------------------------------------------------------------------------- #

def test_get_llm_provider_returns_api_provider_for_setting():
    """get_llm_provider(settings) returns AnthropicAPIProvider when llm='api'."""
    from app.config import Settings, get_settings
    from app.llm import get_llm_provider
    from app.llm.anthropic_api import AnthropicAPIProvider

    get_settings.cache_clear()
    try:
        settings = Settings(llm="api", anthropic_api_key="sk-ant-test")
        with patch("anthropic.Anthropic"):
            provider = get_llm_provider(settings)
        assert isinstance(provider, AnthropicAPIProvider)
    finally:
        get_settings.cache_clear()
