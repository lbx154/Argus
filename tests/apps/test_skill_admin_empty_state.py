"""Regression tests for empty-state skill admin CLI actions."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    shim = Path(__file__).resolve().parents[1] / "subprocess_sitecustomize"
    env["PYTHONPATH"] = str(shim) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "argus_skill", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("--skill-cleanse", "cleanse: no skill files found"),
        ("--skill-compact", "compact: no skill files found"),
    ],
)
def test_empty_skill_admin_actions_no_op_cleanly(
    tmp_path: Path,
    flag: str,
    expected: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    life_dir = tmp_path / "life"

    proc = _run_cli(flag, "--life-dir", str(life_dir), cwd=repo_root)

    assert proc.returncode == 0, proc
    assert proc.stderr == ""
    assert expected in proc.stdout
    assert "not found" not in proc.stdout
    assert not (life_dir / "skills").exists()
