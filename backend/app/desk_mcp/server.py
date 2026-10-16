"""The ``tradedesk`` MCP server: stdio only (no host, port or HTTP transport)."""
# No `from __future__ import annotations`: FastMCP inspects real (not string) annotations.
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from . import tools


@lru_cache(maxsize=1)
def _ctx() -> tools.Ctx:
    return tools.build_ctx()


def create_server(ctx: tools.Ctx | None = None) -> FastMCP:
    mcp = FastMCP("tradedesk")

    def get() -> tools.Ctx:
        return ctx if ctx is not None else _ctx()

    @mcp.tool()
    def status() -> dict:
        """Desk DB path, mode (mock/real), last import time and row counts. Times are PT."""
        return tools.status(get())

    @mcp.tool()
    def rules() -> dict:
        """The trading rules: window, daily stop/target, loss line, numbered rules. Times are PT."""
        return tools.rules(get())

    @mcp.tool()
    def list_days() -> dict:
        """One summary row per trading day, newest first. Times are PT."""
        return tools.list_days(get())

    @mcp.tool()
    def get_day(date: str) -> dict:
        """One day (YYYY-MM-DD): stats, positions with fills and tags, self-report. Times are PT."""
        return tools.get_day(get(), date)

    @mcp.tool()
    def get_trade(id: int) -> dict:
        """One position by trade_id, with its fills and its plan card if any. Times are PT."""
        return tools.get_trade(get(), id)

    @mcp.tool()
    def patterns(date_from: str | None = None, date_to: str | None = None) -> dict:
        """Across-days patterns between two optional YYYY-MM-DD dates, inclusive. Times are PT."""
        return tools.patterns(get(), date_from, date_to)

    @mcp.tool()
    def line_status() -> dict:
        """Loss line P&L and left, plus today's state, stop_hit, target_hit, line_hit. Times are PT."""
        return tools.line_status(get())

    return mcp


def main() -> None:
    create_server().run("stdio")
