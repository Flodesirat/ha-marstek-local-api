"""Regression test for the standalone test_tool.py CLI.

test_tool.py re-implements minimal mocks of the Home Assistant classes it
needs (SensorEntityDescription, RestoreSensor, UnitOfTime, ...) instead of
depending on Home Assistant, then loads the real sensor.py through them. If
sensor.py starts using a field or class the mocks don't provide, that load
crashes at import time (see #6). Running the CLI with `-h` exercises that
module-loading code without needing network access.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_TOOL = REPO_ROOT / "test" / "test_tool.py"


def test_test_tool_loads_integration_modules():
    """test_tool.py must be able to import sensor.py without crashing."""
    result = subprocess.run(
        [sys.executable, str(TEST_TOOL), "-h"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"test_tool.py failed to load:\n{result.stdout}\n{result.stderr}"
    assert "Traceback" not in result.stderr
