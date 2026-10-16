"""Single-shot JSON generation seam (M8c, issue #54).

`app/llm/base.py` is the *frozen, interview-only* `LLMProvider` Protocol — its
docstring forbids adding generation methods. M8c's offline passes (concept
extraction, and later study-sheet generation) need a different shape: one
system+user prompt in, one JSON object out. This module provides that seam
WITHOUT touching the Protocol, by reusing the existing transports:

  - ``none``       → returns None immediately (R8 — degrade to nothing).
  - ``claude-cli`` → binary check + envelope parse via ``app.llm.claude_cli``.
  - ``api``        → ``anthropic_api.generate_json_via_api``.

Every failure path returns None, so callers (the course passes) simply skip the
lesson and write nothing. Logs go to stderr only.

`run_fn` is late-bound to ``app.llm.claude_cli._run_claude`` (referenced at call
time, not captured as a default) so tests can patch the module attribute exactly
like the interview path (issue #49).
"""
from __future__ import annotations

from app.config import Settings, get_settings


def generate_json(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    run_fn=None,
    timeout: int = 120,
) -> dict | None:
    """Generate a single JSON object from a system+user prompt.

    Returns the parsed dict, or None under ``llm=none`` / any failure.
    """
    settings = settings or get_settings()
    backend = settings.llm

    if backend == "none":
        return None

    if backend == "claude-cli":
        # Import the module (not the symbol) and reference the attribute at call
        # time so test patches of app.llm.claude_cli._run_claude take effect.
        from app.llm import claude_cli

        runner = run_fn if run_fn is not None else claude_cli._run_claude
        if not claude_cli._check_binary_available(runner):
            return None
        return claude_cli._invoke_json(system, user, runner, timeout)

    if backend == "api":
        from app.llm import anthropic_api

        return anthropic_api.generate_json_via_api(system, user, settings)

    return None
