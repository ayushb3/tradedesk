.PHONY: desk-mcp desk-mcp-mock desk-mcp-check help dev backend frontend install install-backend install-frontend test tunnel knowledge-ingest knowledge-concepts knowledge-sheets knowledge-install

help:
	@echo "TradeDesk"
	@echo "  make install   - set up backend venv + frontend node_modules"
	@echo "  make dev       - run backend (:9000) + frontend (:5173) together"
	@echo "  make backend   - run backend only"
	@echo "  make frontend  - run frontend only"
	@echo "  make test      - run backend pytest suite"
	@echo "  make tunnel    - expose the frontend publicly via Cloudflare (phone access)"
	@echo ""
	@echo "  Phone access: run 'make dev' in one terminal and 'make tunnel' in another."
	@echo "  cloudflared prints an https://<sub>.trycloudflare.com URL — open it on the phone."
	@echo "  For live HMR on the phone, restart dev as:"
	@echo "    TUNNEL_HOST=<sub>.trycloudflare.com make dev"
	@echo "  (else the app still works; you just refresh manually after edits)."

install: install-backend install-frontend

# pydantic-core (Rust) only ships prebuilt wheels for CPython 3.11-3.13; on 3.14
# pip tries to compile from source and fails. Pick a compatible interpreter.
# Override with: make install PYTHON=python3.11
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3.11)

install-backend:
	@if [ -z "$(PYTHON)" ]; then \
		echo "ERROR: need Python 3.11-3.13 (pydantic-core has no 3.14 wheels yet)."; \
		echo "Install one, e.g.  brew install python@3.12  then rerun 'make install'."; \
		exit 1; \
	fi
	@echo "Using $(PYTHON) ($$($(PYTHON) --version 2>&1))"
	@# If an existing .venv was built with a different interpreter (e.g. python3.14 → 3.12),
	@# `python -m venv .venv` layers the new site-packages on top but leaves the old
	@# python/python3 symlinks intact, silently breaking imports. Recreate in that case.
	@if [ -x backend/.venv/bin/python ] && ! backend/.venv/bin/python -c "import sys; sys.exit(0 if sys.version_info[:2] == $$($(PYTHON) -c 'import sys;print(sys.version_info[:2])') else 1)" 2>/dev/null; then \
		echo "Existing backend/.venv uses a different Python — recreating."; \
		rm -rf backend/.venv; \
	fi
	cd backend && $(PYTHON) -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 9000

frontend:
	cd frontend && npm run dev

# Run both; Ctrl-C stops both.
dev:
	@echo "Starting TradeDesk — backend :9000, frontend :5173"
	@trap 'kill 0' INT TERM; \
	( cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 9000 ) & \
	( cd frontend && npm run dev ) & \
	wait

test:
	cd backend && . .venv/bin/activate && python -m pytest -q

# ── M8 knowledge base ─────────────────────────────────────────────────── #
# Install the optional embedding/vector extras (sentence-transformers, torch).
# The app works without these (deterministic fallback embedder); install for
# real semantic-search quality.
knowledge-install:
	cd backend && . .venv/bin/activate && pip install -r requirements-knowledge.txt

# Ingest the lesson transcripts into SQLite + the vector index. Idempotent:
# re-run any time; only new/changed lessons are reprocessed. Override the source
# with TRADEDESK_LESSONS_DIR=/path make knowledge-ingest. Pass ARGS="--force" to rebuild.
knowledge-ingest:
	cd backend && . .venv/bin/activate && python scripts/ingest_knowledge.py $(ARGS)

# Extract course concepts from the ingested lessons (M8c). Seeds the controlled
# concept vocabulary, then tags each lesson via the TRADEDESK_LLM seam. Under the
# default TRADEDESK_LLM=none it writes nothing (degrades cleanly); set
# TRADEDESK_LLM=claude-cli (or =api) to actually generate. Resumable; pass
# ARGS="--force" to reprocess all, ARGS="--limit 3" for a smoke run.
knowledge-concepts:
	cd backend && . .venv/bin/activate && python scripts/extract_knowledge_concepts.py $(ARGS)

# Generate course study sheets from the ingested lessons (M8c PR-2). For each lesson
# the TRADEDESK_LLM seam distils the transcript into a study sheet (summary /
# rules / optional Mermaid diagram / quiz). Under the default TRADEDESK_LLM=none
# it writes nothing (degrades cleanly); set TRADEDESK_LLM=claude-cli (or =api) to
# actually generate. Resumable via content_hash; pass ARGS="--force" to
# regenerate all, ARGS="--limit 3" for a smoke run.
knowledge-sheets:
	cd backend && . .venv/bin/activate && python scripts/generate_knowledge_sheets.py $(ARGS)

# Expose the running frontend (:5173) at a public https URL for phone testing.
# Requires the Homebrew `cloudflared` binary; does not install it automatically.
# The URL rotates each run — fine for testing, not a stable address.
tunnel:
	@command -v cloudflared >/dev/null 2>&1 || { \
		echo "cloudflared not found. Install with: brew install cloudflared"; \
		exit 1; \
	}
	cloudflared tunnel --url http://localhost:5173

# -- Desk MCP server (stdio) ------------------------------------------------ #
# `make install` creates backend/.venv (POSIX: bin/python; Windows: Scripts/python.exe).
# Override with: make desk-mcp PYTHON=/path/to/python  (or DESK_PYTHON=...).
ifeq ($(origin PYTHON),command line)
DESK_PYTHON ?= $(PYTHON)
endif
DESK_PYTHON ?= $(firstword $(wildcard backend/.venv/Scripts/python.exe backend/.venv/bin/python) python3)

# Run the `tradedesk` MCP server over stdio (real data unless TRADEDESK_MODE=mock).
desk-mcp:
	$(DESK_PYTHON) backend/scripts/desk_mcp.py

# Same server over the synthetic fixture DB (no real data).
desk-mcp-mock:
	TRADEDESK_MODE=mock $(DESK_PYTHON) backend/scripts/desk_mcp.py

# Spawn the server via the MCP client, list tools, call status + line_status, print a summary.
# Mock: TRADEDESK_MODE=mock make desk-mcp-check
desk-mcp-check:
	$(DESK_PYTHON) backend/scripts/desk_mcp_check.py
