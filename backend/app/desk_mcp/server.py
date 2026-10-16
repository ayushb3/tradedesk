"""The ``tradedesk`` MCP server: stdio only (no host, port or HTTP transport)."""
# No `from __future__ import annotations`: FastMCP inspects real (not string) annotations.
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from . import tools, writes


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

    @mcp.tool()
    def import_robinhood(path: str) -> dict:
        """Import fills from a raw-option-orders-*.json inside the export folder. Returns counts only."""
        return writes.import_robinhood(get(), path)

    @mcp.tool()
    def add_plan(
        caller: str, contract: str, size_usd: float, max_loss_usd: float,
        target: str | None = None, note: str | None = None, session_date: str | None = None,
    ) -> dict:
        """Write a plan card (e.g. contract 'SPY 580P'). Shows PT time and whether it matched a trade."""
        return writes.add_plan(
            get(), caller, contract, size_usd, max_loss_usd, target, note, session_date)

    @mcp.tool()
    def annotate_trade(
        trade_id: int, caller: str | None = None, notes: str | None = None,
        grade: str | None = None, tags: list[str] | None = None,
    ) -> dict:
        """Set caller (stored as tag caller:<name>), notes, grade (A/B/C) or add tags on one trade."""
        return writes.annotate_trade(get(), trade_id, caller, notes, grade, tags)

    @mcp.tool()
    def day_fields(date: str, fields: dict) -> dict:
        """Upsert the day self-report (YYYY-MM-DD). Only whitelisted fields; others are rejected."""
        return writes.day_fields(get(), date, **fields)

    return mcp


def main() -> None:
    create_server().run("stdio")
