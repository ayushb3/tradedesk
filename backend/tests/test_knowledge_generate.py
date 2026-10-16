"""M8c PR-1 — generation seam tests (issue #54).

Covers `app.llm.generate.generate_json` across the three backends, plus a
regression that the `claude_cli._invoke_json` refactor did not break the
interview path (`_invoke_claude` still returns a valid InterviewTurn).

The claude-cli tests reuse the `_wrap_in_cli_envelope` + `_BINARY_AVAILABLE=None`
reset pattern from test_llm_backend_swap.py.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app.config import Settings


def _wrap_in_cli_envelope(payload_json: str) -> str:
    """Wrap an assistant JSON string in the claude-cli JSON envelope."""
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


# --------------------------------------------------------------------------- #
# llm=none — degrade to nothing (R8)
# --------------------------------------------------------------------------- #

def test_generate_json_none_returns_none():
    from app.llm.generate import generate_json

    result = generate_json("sys", "user", settings=Settings(llm="none"))
    assert result is None


# --------------------------------------------------------------------------- #
# llm=claude-cli — canned envelope → parsed dict
# --------------------------------------------------------------------------- #

def _make_run_fn(payload: dict):
    """A run_fn that returns --version success and a canned payload envelope."""
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


def test_generate_json_claude_cli_returns_parsed_dict():
    from app.llm.generate import generate_json

    _reset_binary_cache()
    payload = {"concepts": [{"slug": "support-resistance", "weight": 0.9}], "links": []}
    run_fn = _make_run_fn(payload)

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=run_fn
    )
    assert result == payload


def test_generate_json_claude_cli_envelope_error_returns_none():
    from app.llm.generate import generate_json

    _reset_binary_cache()

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        envelope = json.dumps({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": None,
        })
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=envelope, stderr=""
        )

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=_run
    )
    assert result is None


def test_generate_json_claude_cli_non_json_returns_none():
    from app.llm.generate import generate_json

    _reset_binary_cache()

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        # result field holds text that is NOT valid JSON.
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope("this is not json"),
            stderr="",
        )

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=_run
    )
    assert result is None


def test_generate_json_claude_cli_timeout_returns_none():
    from app.llm.generate import generate_json

    _reset_binary_cache()

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=_run
    )
    assert result is None


def test_generate_json_claude_cli_non_object_returns_none():
    from app.llm.generate import generate_json

    _reset_binary_cache()
    # A JSON array is valid JSON but not an object → None.
    run_fn = _make_run_fn(payload={"unused": 1})

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(json.dumps([1, 2, 3])),
            stderr="",
        )

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=_run
    )
    assert result is None


def test_generate_json_claude_cli_binary_missing_returns_none():
    from app.llm.generate import generate_json

    _reset_binary_cache()

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        raise FileNotFoundError("claude: command not found")

    result = generate_json(
        "sys", "user", settings=Settings(llm="claude-cli"), run_fn=_run
    )
    assert result is None
    _reset_binary_cache()


# --------------------------------------------------------------------------- #
# Regression — the refactor did not break the interview path
# --------------------------------------------------------------------------- #

def test_invoke_claude_still_returns_interview_turn():
    """_invoke_claude must still parse a valid InterviewTurn after the
    _invoke_json refactor (the existing interview contract is unchanged)."""
    from app.llm.claude_cli import _invoke_claude
    from app.models.training import InterviewTurn

    turn_json = json.dumps({
        "step": "setup",
        "question": "Walk me through the setup.",
        "options": None,
        "context_note": None,
        "done": False,
    })

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(turn_json),
            stderr="",
        )

    turn = _invoke_claude("sys", "user", _run)
    assert isinstance(turn, InterviewTurn)
    assert turn.step == "setup"
    assert turn.question == "Walk me through the setup."


def test_invoke_json_returns_raw_dict_without_validation():
    """_invoke_json returns the raw parsed dict — even for a shape that would
    fail InterviewTurn validation (it stops one step short)."""
    from app.llm.claude_cli import _invoke_json

    payload = {"anything": "goes", "concepts": []}

    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_wrap_in_cli_envelope(json.dumps(payload)),
            stderr="",
        )

    result = _invoke_json("sys", "user", _run)
    assert result == payload
