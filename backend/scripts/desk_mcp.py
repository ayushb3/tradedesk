"""Run the tradedesk MCP server over stdio: ``python backend/scripts/desk_mcp.py``.

TRADEDESK_MODE=mock serves a synthetic fixture DB (no real data). No port, no HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.desk_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
