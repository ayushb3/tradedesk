"""Desk MCP server (issue #11): no-network guard, id-leak walk, stdio smoke test."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest

from app.desk_mcp import mock, tools

BACKEND = Path(__file__).resolve().parents[1]
SCAN_DIRS = [BACKEND / "app" / "desk_mcp", BACKEND / "app" / "desk"]

FORBIDDEN_MODULES = {
    "requests", "httpx", "urllib", "socket", "subprocess", "aiohttp", "robin_stocks", "webull",
}
FORBIDDEN_DOTTED = {"http.client"}
FORBIDDEN_CALLS = {"os.system", "os.popen"}

FORBIDDEN_KEYS = {"account", "account_number", "order_id", "provider_fill_id"}


# ---------------------------------------------------------------- AST scanner

def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def scan_source(source: str, filename: str = "<src>") -> list[str]:
    """Return violations: forbidden imports and os.system/os.popen calls."""
    hits: list[str] = []
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            if name.split(".")[0] in FORBIDDEN_MODULES or any(
                name == d or name.startswith(d + ".") for d in FORBIDDEN_DOTTED
            ):
                hits.append(f"{filename}:{node.lineno} imports {name}")
        if isinstance(node, ast.Call) and _dotted(node.func) in FORBIDDEN_CALLS:
            hits.append(f"{filename}:{node.lineno} calls {_dotted(node.func)}")
        # `from os import system, popen`
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name in ("system", "popen"):
                    hits.append(f"{filename}:{node.lineno} imports os.{a.name}")
        # dynamic import of a forbidden module
        if isinstance(node, ast.Call) and _dotted(node.func) in ("__import__", "importlib.import_module"):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if node.args[0].value.split(".")[0] in FORBIDDEN_MODULES:
                    hits.append(f"{filename}:{node.lineno} dynamically imports {node.args[0].value}")
    return hits


def scan_dirs(dirs: list[Path]) -> list[str]:
    hits: list[str] = []
    files = [p for d in dirs for p in sorted(d.rglob("*.py"))]
    assert files, "scanner found no files"
    for p in files:
        hits += scan_source(p.read_text(encoding="utf-8"), p.name)
    return hits


def test_no_network_or_process_imports_in_server_and_desk_packages():
    assert scan_dirs(SCAN_DIRS) == []


@pytest.mark.parametrize("snippet", [
    "import requests",
    "import httpx as h",
    "import urllib.request",
    "from urllib import parse",
    "from urllib.parse import quote",
    "import socket",
    "import subprocess",
    "from subprocess import run",
    "import http.client",
    "from http import client",
    "from http.client import HTTPSConnection",
    "import aiohttp",
    "import robin_stocks.robinhood",
    "from webull.core.client import ApiClient",
    "import os\nos.system('x')",
    "import os\nos.popen('x')",
    "from os import system",
    "def f():\n    import requests",
])
def test_scanner_catches_injected_import(snippet, tmp_path):
    mod = tmp_path / "injected.py"
    mod.write_text(snippet + "\n", encoding="utf-8")
    assert scan_dirs([tmp_path]), f"scanner missed: {snippet!r}"


def test_scanner_allows_clean_module(tmp_path):
    (tmp_path / "ok.py").write_text("import json\nimport os\nos.path.join('a')\n", encoding="utf-8")
    assert scan_dirs([tmp_path]) == []


def test_scanner_catches_injected_module_in_scanned_dir():
    """Meta-test on the real scan path: a module dropped in the server package is flagged."""
    injected = BACKEND / "app" / "desk_mcp" / "_injected_for_test.py"
    injected.write_text("import requests\n", encoding="utf-8")
    try:
        assert any("requests" in h for h in scan_dirs(SCAN_DIRS))
    finally:
        injected.unlink()


# ------------------------------------------------------------------ mock tools

@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    from app.db.connection import get_engine

    path = tmp_path_factory.mktemp("mockdb") / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


def _walk(node, path="$"):
    """Yield (path, key_or_None, scalar_or_None) for every key and leaf."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k, None
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, None, node


def _all_outputs(ctx) -> dict[str, dict]:
    outs = {
        "status": tools.status(ctx),
        "rules": tools.rules(ctx),
        "list_days": tools.list_days(ctx),
        "patterns": tools.patterns(ctx),
        "patterns_range": tools.patterns(ctx, "2026-10-09", "2026-10-12"),
        "line_status": tools.line_status(ctx),
    }
    days = [d["date"] for d in outs["list_days"]["days"]]
    assert days, "mock fixture produced no days"
    for d in days:
        outs[f"get_day {d}"] = tools.get_day(ctx, d)
        for pos in outs[f"get_day {d}"]["positions"]:
            outs[f"get_trade {pos['trade_id']}"] = tools.get_trade(ctx, pos["trade_id"])
    return outs


def test_every_tool_output_carries_tz_and_as_of(ctx):
    for name, out in _all_outputs(ctx).items():
        assert out["tz"] == "America/Los_Angeles", name
        assert "as_of" in out, name
        assert out["as_of"] is not None, name


def test_no_account_or_order_ids_in_any_output(ctx):
    outs = _all_outputs(ctx)
    for name, out in outs.items():
        out = json.loads(json.dumps(out))  # must be JSON-able
        for path, key, value in _walk(out):
            assert key not in FORBIDDEN_KEYS, f"{name}: forbidden key at {path}"
            if isinstance(value, str):
                assert not value.startswith("rh-"), f"{name}: broker-style id at {path}"
                assert not re.fullmatch(r"\d{8,}", value), f"{name}: id-like number at {path}"
                assert not re.fullmatch(r"m\d-[bs]", value), f"{name}: synthetic order id at {path}"


def test_mock_numbers_and_plan(ctx):
    days = {d["date"]: d for d in tools.list_days(ctx)["days"]}
    assert set(days) == {"2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"}
    # m1: buy 2 @1.00 (fee 1) sell 2 @1.60 (fee 1) -> +120 - 2 = 118
    assert days["2026-10-08"]["net"] == pytest.approx(118.0)
    day = tools.get_day(ctx, "2026-10-08")
    tid = day["positions"][0]["trade_id"]
    t = tools.get_trade(ctx, tid)
    assert t["trade"]["ticker"] == "ZORP" and len(t["trade"]["fills"]) == 2
    assert t["plan"] is not None and t["plan"]["caller"] == "mentor-a"
    assert t["trade"]["fills"][0]["ts_pt"].endswith(" PT")
    other = tools.get_trade(ctx, tools.get_day(ctx, "2026-10-09")["positions"][0]["trade_id"])
    assert other["plan"] is None


def test_line_status_shape(ctx):
    ls = tools.line_status(ctx)
    assert set(ls["today"]) >= {"net", "stop_hit", "target_hit", "line_hit", "window"}
    assert isinstance(ls["today"]["line_hit"], bool)
    assert ls["state"] in ("before_window", "in_window", "closed")


def test_errors_for_bad_input(ctx):
    with pytest.raises(ValueError):
        tools.get_day(ctx, "2026-01-02")
    with pytest.raises(ValueError):
        tools.get_day(ctx, "not-a-date")
    with pytest.raises(ValueError):
        tools.get_trade(ctx, 999999)


def test_build_ctx_mock_mode_uses_temp_fixture_db(monkeypatch, tmp_path):
    monkeypatch.setattr(mock.tempfile, "gettempdir", lambda: str(tmp_path))
    c = tools.build_ctx()  # conftest pins TRADEDESK_MODE=mock
    assert c.mode == "mock"
    assert Path(c.db_path) == tmp_path / mock.MOCK_DB_NAME and Path(c.db_path).exists()
    assert c.rules.account_size.source_path == "mock/does-not-exist.json"
    assert tools.status(c)["counts"]["days"] == 4


# ------------------------------------------------------------- stdio end to end

def test_stdio_server_lists_tools_and_serves_line_status(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "TRADEDESK_MODE": "mock", "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "TMPDIR": str(tmp_path)}
    params = StdioServerParameters(
        command=sys.executable, args=[str(BACKEND / "scripts" / "desk_mcp.py")],
        env=env, cwd=str(BACKEND),
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                listed = await session.list_tools()
                line = await session.call_tool("line_status", {})
                day = await session.call_tool("get_day", {"date": "2026-10-13"})
                return init, listed, line, day

    init, listed, line, day = asyncio.run(asyncio.wait_for(run(), timeout=90))
    assert init.serverInfo.name == "tradedesk"
    names = {t.name for t in listed.tools}
    assert names == {
        "status", "rules", "list_days", "get_day", "get_trade", "patterns", "line_status",
    }
    assert not line.isError
    payload = json.loads(line.content[0].text)
    assert payload["tz"] == "America/Los_Angeles" and "line_hit" in payload["today"]
    assert not day.isError and json.loads(day.content[0].text)["stats"]["positions"] == 2
