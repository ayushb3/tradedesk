# Desk page: mock-mode demo and checks

The desk page (`desk-page/index.html`) is the Fill Ledger layout fed live by the local `tradedesk`
MCP server. It is published as a private Claude artifact (the link lives in the owner's vault, not
here) and declares one host server:

```json
{"mcp": {"servers": [{"server": "host:tradedesk", "tools": ["line_status", "list_days", "get_day", "patterns"]}]}}
```

It reads with `watchTool` (60 s refetch for the line, the day list and the open day; 120 s for
patterns), keeps no copy of the data, and holds no keys or ids beyond TradeDesk's own `trade_id`.

## Run it against mock data

1. Register the server in the Claude desktop app (see `backend/scripts/README.md`, "Desk MCP
   server"), with `"env": {"TRADEDESK_MODE": "mock"}` for a no-real-money demo.
2. `TRADEDESK_MODE=mock make desk-mcp-check` should print `mode: mock` and the tool list.
3. Restart the desktop app, open the artifact link there, and allow `tradedesk` when asked.

## What to check

| State | How to get it | Expected |
|---|---|---|
| Live | server registered and running | green dot, "live · imported <date time> PT" |
| No server | open the page in a browser, or stop the server | grey banner "TradeDesk server isn't running" (or "isn't reachable from this view" outside the app), no numbers |
| Not allowed | turn tradedesk off in the page's Permissions | banner "TradeDesk is turned off for this page" |
| Refresh failed | server errors after a good read | amber dot "stale", last good numbers stay |
| Loss line | always on top | $ left, P&L since the start date, meter, today's window state, stop/target chips |
| Day | tap a day in the strip or the table | net, KPIs, rule chips, plan cards, positions that open into fills (time PT), lessons |
| No days | empty DB | "No days in TradeDesk yet" |

Writes, "Ask Claude", the line-hit lock and the remaining odd states come with issue #17; the phone
snapshot with #18.
