# Desk page: mock-mode demo and checks

The desk page (`desk-page/index.html`) is the Fill Ledger layout fed live by the local `tradedesk`
MCP server. It is published as a private Claude artifact (the link lives in the owner's vault, not
here) and declares one host server:

```json
{"mcp": {"servers": [{"server": "host:tradedesk", "tools": ["line_status", "list_days", "get_day", "patterns", "annotate_trade", "add_plan"]}]}}
```

It reads with `watchTool` (60 s refetch for the line, the day list and the open day; 120 s for
patterns), keeps no copy of the data, and holds no keys or ids beyond TradeDesk's own `trade_id` / `trade_key`.

Two tools write: `annotate_trade` (caller, grade, notes on one position) and `add_plan` (a plan card on the open day). Each is called once per click, never retried by the page, and followed by `invalidate("host:tradedesk", "get_day")` so the open day refetches. "Ask Claude" only copies a prompt; it calls nothing.

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
| No days | empty DB | strip says "No days in TradeDesk yet. Run the export, then ask Claude to import it."; the day area shows the same with an "Ask Claude to import" copy button (mock mode always has days; fake it by pointing the server at an empty DB) |
| Open positions | a position with no exit yet (`status: "open"`, `pnl: null`) | position shows "still open" and P&L "open"; the day gets a "1 still open" chip and a note |
| Export older than today | a weekday, window closed (after 08:30 PT), newest imported fill dated before today (PT) | amber note under the line: "Last import is from <date>; run the export after the close." Mock mode shows it as soon as the window has closed, because the synthetic fills are older than today |
| Stop hit today | today's net at or below the daily stop | red note "Daily stop hit (...). Done for today." plus the stop chip |
| No plans today | a weekday, before or during the window, no plan card for today | amber note "No plan cards yet today — rule 4." on the line panel with an "Add plan card" button; the same note shows on today's day view (today appears as a "today" tile in the strip when it has no fills yet). Mock mode has no plans for today, so it shows from the open of the window |
| Line hit | `line_status.left <= 0` or `today.line_hit` | only the line panel and the reset list ("Trading is over for this line. Close the platform. Step away from the desk. Talk to Claude about the reset plan, not the trades.") show; days, table, detail and every form are hidden. In mock mode lower `loss_line.amount` in the example rules file below the synthetic P&L (about −$322, so 300 works) |
| Edit a position | tap "Edit" on a position | inline form (caller prefilled from the plan card, grade, notes); "Saving...", then "Saved ..." or the error; one `annotate_trade` call; disabled while saving |
| Add plan card | tap "Add plan card" in a day | inline form; client-side number checks; one `add_plan` with `session_date` = the open day; the card appears after the refetch |
| Write fails | decline the app's confirm, or stop the server mid-write | "you declined the confirm" (nothing written); a server message for `tool_error`; for a timeout "may or may not have saved" (the page re-reads and waits for another click, never retries); no server hides every form |
| Ask Claude | tap it on a day or a position | copies `Review my day <date> with get_day: ...` or `Review trade <trade_key> (trade_id N) on <date> with get_trade`; if the clipboard is blocked, the text is shown selected in a box |

The phone snapshot comes with issue #18.
