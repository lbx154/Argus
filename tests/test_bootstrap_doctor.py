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


def test_bootstrap_accepts_current_editable_python_without_checkout_venv(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Argus"
    (root / "argus_skill").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='argus-skill'\n", encoding="utf-8")

    report = argus_doctor.run_bootstrap_doctor(root)
    python = next(
        item for item in report["findings"] if item["code"] == "ARGUS-PYTHON-002"
    )
    core = next(
        item for item in report["findings"] if item["code"] == "ARGUS-PYTHON-003"
    )

    assert python["ok"] is True
    assert "without checkout .venv" in python["detail"]
    assert core["ok"] is True


def test_bootstrap_desktop_runtime_is_advisory_for_cli_web(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Argus"
    (root / "argus_skill").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='argus-skill'\n",
        encoding="utf-8",
    )
    desktop = root / "desktop-tauri"
    desktop.mkdir()
    (desktop / "package.json").write_text("{}\n", encoding="utf-8")

    report = argus_doctor.run_bootstrap_doctor(root)

    desktop = next(
        item for item in report["findings"] if item["code"] == "ARGUS-DESKTOP-001"
    )
    assert desktop["ok"] is True
    assert "optional for CLI/Web" in desktop["detail"]


def test_bootstrap_repair_requires_explicit_yes(capsys) -> None:
    rc = argus_doctor.main(["--repair-install"])

    assert rc == 3
    assert "requires --yes" in capsys.readouterr().err


def test_bootstrap_install_uses_only_registered_venv_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Argus"
    (root / "argus_skill").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='argus-skill'\n", encoding="utf-8")
    runtime = root / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        argus_doctor,
        "_run_repair_command",
        lambda command, **kwargs: calls.append((command, kwargs)) or {"status": "applied"},
    )

    actions = argus_doctor.run_bootstrap_repair(root, install=True)

    assert actions[0]["id"] == "install_editable"
    assert calls == [
        ([runtime, "-m", "pip", "install", "-e", str(root.resolve())], {
            "cwd": root.resolve(),
            "timeout": 600,
        })
    ]


def test_bootstrap_doctor_reports_missing_checkout_without_crashing(tmp_path: Path) -> None:
    report = argus_doctor.run_bootstrap_doctor(tmp_path / "missing")

    assert report["ok"] is False
    install = next(item for item in report["findings"] if item["code"] == "ARGUS-INSTALL-001")
    assert install["ok"] is False
    assert "--root" in install["fix"]


def test_bootstrap_doctor_uses_current_python_when_checkout_venv_is_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(argus_doctor, "_venv_python", lambda _root: None)

    report = argus_doctor.run_bootstrap_doctor(ROOT)

    runtime = next(
        item for item in report["findings"] if item["code"] == "ARGUS-PYTHON-002"
    )
    core_import = next(
        item for item in report["findings"] if item["code"] == "ARGUS-PYTHON-003"
    )
    assert runtime["ok"] is True
    assert "bootstrap fallback" in runtime["detail"]
    assert core_import["ok"] is True
