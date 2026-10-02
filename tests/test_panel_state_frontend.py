"""Behavior tests for the panel's browser-local preferences (#52)."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_panel_state_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test",
         str(Path(__file__).parent / "frontend" / "panel_state.test.mjs")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
