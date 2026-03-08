"""Pylint quality gate — ensures the integration source stays clean."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO_ROOT / "custom_components" / "marstek_local_api"
PYLINTRC = REPO_ROOT / ".pylintrc"


def test_pylint_integration_source():
    """Run pylint on the integration and fail if the score drops below 10.00."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pylint",
            f"--rcfile={PYLINTRC}",
            "--fail-under=10.0",
            str(SOURCE_PATH),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        output = result.stdout + result.stderr
        # Extract only the meaningful lines (issues + score), skip empty lines
        lines = [l for l in output.splitlines() if l.strip() and not l.startswith("---")]
        report = "\n".join(lines)
        raise AssertionError(f"Pylint reported issues:\n\n{report}")
