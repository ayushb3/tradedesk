"""ClaudeCLIProvider — LLM backend that shells out to `claude -p --output-format json`.

Uses the operator's Claude Pro/Max subscription (OAuth, no key). Post-2026-06-15
policy: `claude -p` draws from the plan's monthly Agent SDK credit pool at API
rates — fine for per-day debrief volume.

The `claude` binary reads OAuth credentials from ~/.config/claude/ at startup;
nothing is passed on the command line. No secrets in argv, logs, or the DB.

CLI JSON envelope (verified 2026-07-09 against claude v1.x):
  {
    "type": "result",
    "subtype": "success" | "error_during_execution",
    "is_error": false | true,
    "result": "<assistant text>",     # present when is_error=false
    "api_error_status": null | <int>,
    ...
  }
The `result` key holds the raw assistant text. We instruct Claude to emit a
single JSON object (InterviewTurn) as its entire response, then parse result
as JSON and validate with Pydantic.

Source: observed output of `echo "..." | claude -p --output-format json --tools ""`
(live run on this machine, 2026-07-09). No official docs page pins the exact
shape — the envelope is the Claude Code SDK / headless-mode wire format and may
change across CLI versions. If the shape shifts, the parser falls back gracefully.

Graceful-degradation hierarchy (all paths return a valid InterviewTurn):
  FileNotFoundError  → binary absent; cached per-process; fallback to FormProvider
  TimeoutExpired     → 60 s hard limit; fallback
  Non-zero exit code → auth/rate-limit/network; fallback
  Non-JSON stdout    → parse error; fallback
  Shape mismatch     → Pydantic validation error; fallback

TODO(6.2.x): Full tool wiring (save_enrichment / create_scenario as callable MCP
tools via --mcp-config) is deferred to a future task. v1 stuffs all context into
the system prompt and lets finish_debrief persist artifacts through the existing
store path. InterviewTurn would need a tool_call field to carry tool-use turns —
that is a models/training.py change owned by the PM.

data-eng task 6.2.1.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Callable

from app.config import Settings
from app.llm.base import DebriefContext
from app.llm.prompts import build_prompt_next, build_prompt_start  # noqa: F401 — re-exported for tests
from app.models.training import DebriefReply, DebriefStep, InterviewTurn

# --------------------------------------------------------------------------- #
# Module-level binary-existence cache
# --------------------------------------------------------------------------- #
# Sentinel: None = not yet checked, True = present, False = absent.
# Cached for the lifetime of the process so we don't spawn a subprocess on
# every single interview turn just to discover the binary is missing.
_BINARY_AVAILABLE: bool | None = None

_CLAUDE_BIN = "claude"
_TIMEOUT_S = 60


def _check_binary_available(run_fn: _RunFn) -> bool:
    """Return True if the `claude` binary is reachable. Cached per-process.

    Uses run_fn (injectable for tests) rather than shutil.which so the cache
    test can count actual invocations.
    """
    global _BINARY_AVAILABLE
    if _BINARY_AVAILABLE is None:
        try:
            run_fn(
                [_CLAUDE_BIN, "--version"],
                input="",
                capture_output=True,
                text=True,
                timeout=5,
            )
            _BINARY_AVAILABLE = True
        except FileNotFoundError:
            _BINARY_AVAILABLE = False
    return _BINARY_AVAILABLE


# --------------------------------------------------------------------------- #
# Type alias for the injected runner
# --------------------------------------------------------------------------- #
_RunFn = Callable[..., subprocess.CompletedProcess]


def _run_claude(
    args: list[str],
    input: str,
    capture_output: bool,
    text: bool,
    timeout: int,
) -> subprocess.CompletedProcess:
    """Default runner — wraps subprocess.run. Injectable for tests."""
    return subprocess.run(
        args,
        input=input,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# CLI invocation & parsing
# --------------------------------------------------------------------------- #

def _invoke_json(
    system_prompt: str,
    user_message: str,
    run_fn: _RunFn,
    timeout: int = _TIMEOUT_S,
) -> dict | None:
    """Shell out to `claude -p --output-format json` and return the assistant's
    `result` text parsed as a JSON object (dict), or None on any failure.

    This is the shared envelope parser: it stops one step short of Pydantic
    validation so both the interview path (`_invoke_claude`) and the M8c
    single-shot generation seam (`app.llm.generate`) can reuse it. Callers apply
    their own `model_validate` to the returned dict.

    On failure, logs a summary to stderr (never to stdout — callers read
    return values; logs go to stderr so they don't corrupt route responses).
    """
    cmd = [
        _CLAUDE_BIN,
        "-p",
        "--output-format", "json",
        "--tools", "",          # disable all built-in tools — interview only
        "--system-prompt", system_prompt,
    ]
    try:
        result = run_fn(
            cmd,
            input=user_message,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[claude-cli] timeout after {timeout}s — falling back to form backend",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        # Should not reach here if _check_binary_available was already called,
        # but guard anyway.
        print(
            "[claude-cli] binary not found — falling back to form backend",
            file=sys.stderr,
        )
        return None

    if result.returncode != 0:
        # Parse stderr for a hint; log exit code only (not the prompt, which
        # may contain trade P&L and tags — user data).
        hint = (result.stderr or "").strip()[:200] if result.stderr else ""
        print(
            f"[claude-cli] exited {result.returncode}; hint: {hint!r} — "
            "falling back to form backend",
            file=sys.stderr,
        )
        return None

    # Parse the CLI JSON envelope.
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"[claude-cli] envelope parse error: {exc} — falling back",
            file=sys.stderr,
        )
        return None

    # Extract the assistant text from the `result` field.
    if envelope.get("is_error") or envelope.get("subtype") == "error_during_execution":
        print(
            f"[claude-cli] envelope reports error (is_error={envelope.get('is_error')}) "
            "— falling back",
            file=sys.stderr,
        )
        return None

    assistant_text = envelope.get("result")
    if not isinstance(assistant_text, str):
        print(
            "[claude-cli] envelope missing 'result' string — falling back",
            file=sys.stderr,
        )
        return None

    # Parse the assistant's text as a JSON object.
    try:
        payload = json.loads(assistant_text)
    except json.JSONDecodeError as exc:
        print(
            f"[claude-cli] assistant JSON parse error: {exc} — falling back",
            file=sys.stderr,
        )
        return None

    if not isinstance(payload, dict):
        print(
            f"[claude-cli] assistant JSON is {type(payload).__name__}, expected object "
            "— falling back",
            file=sys.stderr,
        )
        return None

    return payload


def _invoke_claude(
    system_prompt: str,
    user_message: str,
    run_fn: _RunFn,
    timeout: int = _TIMEOUT_S,
) -> InterviewTurn | None:
    """Shell out to `claude -p --output-format json` and return a parsed
    InterviewTurn, or None on any failure.

    Delegates the CLI-envelope parsing to `_invoke_json`, then validates the
    parsed dict as an InterviewTurn. Behavior is identical to the pre-refactor
    inline implementation.
    """
    turn_data = _invoke_json(system_prompt, user_message, run_fn, timeout)
    if turn_data is None:
        return None

    # Validate shape with Pydantic.
    try:
        return InterviewTurn.model_validate(turn_data)
    except Exception as exc:
        print(
            f"[claude-cli] InterviewTurn shape mismatch: {exc} — falling back",
            file=sys.stderr,
        )
        return None


# --------------------------------------------------------------------------- #
# ClaudeCLIProvider
# --------------------------------------------------------------------------- #

class ClaudeCLIProvider:
    """LLM backend using `claude -p --output-format json`.

    Satisfies LLMProvider (app/llm/base.py, Protocol). On any failure the
    provider falls back to the FormProvider so the debrief always completes.

    Parameters
    ----------
    settings:
        App settings (reserved for future use; unused in v1 — the CLI reads
        OAuth from ~/.config/claude/ itself; nothing to configure here).
    _run:
        Injectable subprocess runner. Default (when None): the module-level
        _run_claude, looked up at construction time. Tests patch
        app.llm.claude_cli._run_claude and expect new instances to pick it up —
        so the default MUST be resolved late here, not captured as a default-arg
        value at function-definition time (which would freeze the original
        runner and defeat the patch, shelling out to the real `claude` binary).
    """

    name: str = "claude-cli"

    def __init__(
        self,
        settings: Settings | None = None,
        _run: _RunFn | None = None,
    ) -> None:
        self._settings = settings
        # Late-bind the default so test patches of the module attribute take
        # effect (see issue #49). Do NOT change to `_run: _RunFn = _run_claude`.
        self._run = _run if _run is not None else _run_claude

    # ---------------------------------------------------------------------- #
    # LLMProvider protocol
    # ---------------------------------------------------------------------- #

    def start_interview(self, ctx: DebriefContext) -> InterviewTurn:
        """Return the setup question, generated by Claude.

        Falls back to FormProvider.start_interview on any failure.
        """
        if not _check_binary_available(self._run):
            print(
                "[claude-cli] binary absent — falling back to form backend for start_interview",
                file=sys.stderr,
            )
            return self._form_start(ctx)

        system, user = build_prompt_start(ctx)
        turn = _invoke_claude(system, user, self._run)
        if turn is None:
            return self._form_start(ctx)
        return turn

    def next_turn(
        self,
        ctx: DebriefContext,
        transcript: list[dict],
        reply: DebriefReply,
    ) -> InterviewTurn:
        """Return the next interview turn, generated by Claude.

        Falls back to FormProvider.next_turn / summarize_transcript on any failure.
        """
        if not _check_binary_available(self._run):
            print(
                "[claude-cli] binary absent — falling back to form backend for next_turn",
                file=sys.stderr,
            )
            return self._form_next(ctx, transcript, reply)

        system, user = build_prompt_next(ctx, transcript, reply)
        turn = _invoke_claude(system, user, self._run)
        if turn is None:
            return self._form_next(ctx, transcript, reply)
        return turn

    # ---------------------------------------------------------------------- #
    # Fallback helpers
    # ---------------------------------------------------------------------- #

    def _form_start(self, ctx: DebriefContext) -> InterviewTurn:
        """Delegate start_interview to the form backend."""
        from app.llm.form import FormProvider
        return FormProvider().start_interview(ctx)

    def _form_next(
        self,
        ctx: DebriefContext,
        transcript: list[dict],
        reply: DebriefReply,
    ) -> InterviewTurn:
        """Delegate next_turn to the form backend."""
        from app.llm.form import FormProvider
        return FormProvider().next_turn(ctx, transcript, reply)
