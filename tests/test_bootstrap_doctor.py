from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import argus_doctor

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_doctor_runs_without_importing_argus_core() -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "argus_doctor.py"), "--root", str(ROOT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["mode"] == "bootstrap"
    assert report["ok"] is True
    assert {item["code"] for item in report["findings"]} >= {
        "ARGUS-HOST-001",
        "ARGUS-INSTALL-001",
        "ARGUS-PYTHON-003",
        "ARGUS-WEB-001",
    }


def test_bootstrap_doctor_reports_missing_checkout_without_crashing(tmp_path: Path) -> None:
    report = argus_doctor.run_bootstrap_doctor(tmp_path / "missing")

    assert report["ok"] is False
    install = next(item for item in report["findings"] if item["code"] == "ARGUS-INSTALL-001")
    assert install["ok"] is False
    assert "--root" in install["fix"]
