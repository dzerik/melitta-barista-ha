"""Run the real panel recipe-editor milk controls in a Node VM."""

from pathlib import Path
import subprocess


def test_panel_milk_mode_controls_and_payloads():
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "--test", "tests/frontend/milk_mode.test.mjs"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
