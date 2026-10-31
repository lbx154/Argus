"""PR packaging checks retain all desktop targets and shared compatibility tests."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_compatibility_checks_use_available_runners_and_do_not_fail_fast():
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop-trial.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["desktop"]
    runners = job["strategy"]["matrix"]["os"]
    assert "macos-14" not in runners  # Retired GitHub image, not a code/test failure.
    assert "windows-latest" in runners
    assert any(name.startswith("macos-") and name.endswith("-intel") for name in runners)
    assert any(name.startswith("macos-") and not name.endswith("-intel") for name in runners)
    assert job["strategy"]["fail-fast"] is False
    assert not job.get("continue-on-error", False)
    assert all(not step.get("continue-on-error", False) for step in job["steps"])


def test_packaging_installs_root_contract_tools_and_keeps_shared_trial_tests():
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop-trial.yml").read_text(encoding="utf-8"))
    commands = [step.get("run", "") for step in workflow["jobs"]["desktop"]["steps"]]
    assert any("npm ci" in command.splitlines() for command in commands)
    assert any("python -m argus.release_tools.build_release" in command for command in commands)
    assert any("tests/trial/test_desktop.py tests/trial/test_client.py tests/desktop" in command for command in commands)
    assert any("npm --prefix desktop-tauri run ui:typecheck" in command for command in commands)
    assert any("--lib --test core_tests" in command for command in commands)
