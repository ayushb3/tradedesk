# backend/scripts — Webull setup utilities

## webull_auth.py — One-time token bootstrap

Before you can run TradeDesk in live Webull mode (`TRADEDESK_MODE=webull`)
you must complete a one-time SMS verification to obtain an access token.
This script drives that flow.

### Prerequisites

1. You have created a Webull OpenAPI application and noted your
   **App Key** and **App Secret** from the developer portal.
2. The new SDK is installed: `pip install webull-openapi-python-sdk`
   (already in `requirements.txt`; `make install` handles this).
3. Your `.env` file has `WEBULL_APP_KEY=` and `WEBULL_APP_SECRET=` filled in.

### Run it

```bash
cd /path/to/tradedesk
python backend/scripts/webull_auth.py
```

The script will:

1. Call `POST /openapi/auth/token/create` using your AK/SK.
2. Print a prompt:
   > Open the Webull mobile app → Messages → OpenAPI Notifications →
   > Check Now → enter the SMS code.
3. Poll `POST /openapi/auth/token/check` every 5 s (up to 5 min) until
   the status becomes `NORMAL`.
4. Write the verified token to `data/webull-token/token.txt` (gitignored).
5. Exit 0 on success.

**Expected wall-clock time:** 30 s – 2 min depending on how quickly you
complete the in-app verification.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--token-dir /abs/path` | `data/webull-token/` | Override token storage directory |
| `--interval N` | `5` | Seconds between polls |
| `--timeout N` | `300` | Maximum seconds to wait for verification |

### Token directory

By default tokens are stored at `<repo_root>/data/webull-token/token.txt`.
This directory is gitignored.  To use a custom path, either:

- Set `WEBULL_OPENAPI_TOKEN_DIR=/absolute/path` in your `.env`, **or**
- Pass `--token-dir /absolute/path` on the command line.

The Webull SDK reads `WEBULL_OPENAPI_TOKEN_DIR` automatically for runtime
use, so the path set here must match what you put in `.env`.

### Token validity

Access tokens are valid for **15 days**.  Re-run this script when the token
expires (the provider will raise a `WebullTokenPending` error pointing here).

### After success

```bash
TRADEDESK_MODE=webull make dev
```

The live provider reads the token from disk automatically.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Token is NORMAL and written to disk. |
| `1` | Timeout — SMS not verified within the timeout window. Run again. |
| `2` | Unexpected error (bad credentials, network, SDK not installed). |

## import_robinhood.py — Robinhood option fills from the read-only export

Loads `raw-option-orders-<start>.json` (written by the separate, guarded
read-only exporter; never commit it) into the fills table and rebuilds trades.
No network, no login, no order entry.

```bash
python backend/scripts/import_robinhood.py ../rh-export/out/raw-option-orders-2026-09-29.json --db data/robinhood.db
TRADEDESK_DB_PATH=$PWD/data/robinhood.db make backend
```

Re-running with the same or a newer overlapping export is safe (fills are keyed
by Robinhood order id). Keep real data in its own DB file: the mock provider's
Import button is refused (409) once a DB holds real fills.

## Upgrading an older local database (legacy table prefix)

Earlier local builds stored the knowledge-base tables under a different name
prefix. If you have such a database and want to keep its data, set
`TRADEDESK_LEGACY_TABLE_PREFIX` once to the old prefix (for example `old_`) and
start the backend (or run any script) one time:

```bash
TRADEDESK_LEGACY_TABLE_PREFIX=old_ make backend
```

On startup, every table named `<prefix><name>` (e.g. `old_lessons`) is renamed
to `knowledge_<name>` (e.g. `knowledge_lessons`), but only when the
`knowledge_*` table does not exist yet, so the step is idempotent and never
overwrites data. You can unset the variable afterwards. When the variable is
unset (the default) the migration does nothing.

## Knowledge-base scripts

- `ingest_knowledge.py` -- ingest your lesson transcripts (`TRADEDESK_LESSONS_DIR`,
  optional `TRADEDESK_MODULES_DIR`); also `make knowledge-ingest`.
- `extract_knowledge_concepts.py` -- LLM concept-tagging pass (`make knowledge-concepts`).
  The vocabulary comes from `TRADEDESK_SEED_CONCEPTS` or the bundled
  `app/knowledge/seed_concepts.example.json`.
- `generate_knowledge_sheets.py` -- LLM study-sheet pass (`make knowledge-sheets`).

## Desk MCP server

`desk_mcp.py` runs the `tradedesk` MCP server over stdio (read-only; no port, no network).
It serves real data from the desk DB unless `TRADEDESK_MODE=mock`.

```bash
make desk-mcp          # real data (data/desk.db, data/rules.yaml)
make desk-mcp-mock     # synthetic fixture DB
make desk-mcp-check    # spawn the server, list tools, call status + line_status, print a summary
```

The Makefile targets use `backend/.venv` (`Scripts/python.exe` on Windows, `bin/python` elsewhere);
override with `make desk-mcp PYTHON=/path/to/python`. Optional env for any client config below:
`TRADEDESK_DB_PATH`, `TRADEDESK_RULES_PATH`, `TRADEDESK_EXPORT_DIR`, `TRADEDESK_MODE`. Use absolute
paths for the command and the script so the entry does not depend on the working directory.
Replace `C:\path\to\tradedesk` with your clone.

### Claude desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json` (merge into any existing `mcpServers`),
then fully restart the desktop app:

```json
{
  "mcpServers": {
    "tradedesk": {
      "command": "C:\path\to\tradedesk\backend\.venv\Scripts\python.exe",
      "args": ["C:\path\to\tradedesk\backend\scripts\desk_mcp.py"],
      "env": {
        "TRADEDESK_DB_PATH": "C:\path\to\tradedesk\data\desk.db",
        "TRADEDESK_RULES_PATH": "C:\path\to\tradedesk\data\rules.yaml"
      }
    }
  }
}
```

The `env` block is optional (defaults are `data/desk.db` and `data/rules.yaml` in the repo).

### Claude Code (another repo or vault)

Project-scoped `.mcp.json` at that repo's root:

```json
{
  "mcpServers": {
    "tradedesk": {
      "type": "stdio",
      "command": "C:\path\to\tradedesk\backend\.venv\Scripts\python.exe",
      "args": ["C:\path\to\tradedesk\backend\scripts\desk_mcp.py"],
      "env": {
        "TRADEDESK_DB_PATH": "C:\path\to\tradedesk\data\desk.db",
        "TRADEDESK_RULES_PATH": "C:\path\to\tradedesk\data\rules.yaml"
      }
    }
  }
}
```

Equivalent one-liner (run inside that repo; `--scope project` writes `.mcp.json`):

```bash
claude mcp add tradedesk --scope project \
  -e TRADEDESK_DB_PATH='C:\path\to\tradedesk\data\desk.db' \
  -e TRADEDESK_RULES_PATH='C:\path\to\tradedesk\data\rules.yaml' \
  -- 'C:\path\to\tradedesk\backend\.venv\Scripts\python.exe' 'C:\path\to\tradedesk\backend\scripts\desk_mcp.py'
```

On macOS/Linux use `.../backend/.venv/bin/python`. Verify with `claude mcp list`
(or `make desk-mcp-check`); the server exposes 7 tools.
