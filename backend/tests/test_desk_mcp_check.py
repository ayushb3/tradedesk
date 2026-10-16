"""desk_mcp_check.py runs end to end in mock mode and lists the desk tools."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_desk_mcp_check_mock(tmp_path):
    env = {**os.environ, "TRADEDESK_MODE": "mock", "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "TMPDIR": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "desk_mcp_check.py")],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mode   : mock" in proc.stdout
    m = re.search(r"tools  : (\d+)", proc.stdout)
    assert m and int(m.group(1)) >= 7, proc.stdout
