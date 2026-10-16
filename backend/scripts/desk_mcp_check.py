"""Smoke-check the tradedesk MCP server: spawn it over stdio, list tools, call status + line_status.

Usage: ``python backend/scripts/desk_mcp_check.py`` (TRADEDESK_MODE=mock for the fixture DB).
Prints a one-screen summary and exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "desk_mcp.py"
BACKEND = SERVER.parents[1]


def _payload(result) -> dict:
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "tool error")
    return json.loads(result.content[0].text)


async def _run() -> dict:
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], env={**os.environ}, cwd=str(BACKEND),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            status = _payload(await session.call_tool("status", {}))
            line = _payload(await session.call_tool("line_status", {}))
    return {"server": init.serverInfo.name, "tools": tools, "status": status, "line": line}


def _line_left(line: dict):
    for key in ("left", "line_left", "remaining"):
        if key in line:
            return line[key]
    for v in line.values():
        if isinstance(v, dict) and "left" in v:
            return v["left"]
    return "n/a"


def main() -> int:
    try:
        r = asyncio.run(asyncio.wait_for(_run(), timeout=90))
    except Exception as exc:  # noqa: BLE001 - report any failure as a failed check
        print(f"desk-mcp-check FAILED: {exc!r}")
        return 1
    st = r["status"]
    print("desk-mcp-check")
    print(f"  server : {r['server']}")
    print(f"  mode   : {st.get('mode')}")
    print(f"  db     : {st.get('db_path')}")
    print(f"  tools  : {len(r['tools'])} ({', '.join(r['tools'])})")
    print(f"  counts : {st.get('counts')}")
    print(f"  line   : left={_line_left(r['line'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
