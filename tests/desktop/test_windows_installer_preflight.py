"""Windows installer policy uses isolated metadata fixtures, never user profiles."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "desktop-tauri/scripts/installer-preflight.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer metadata API")


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
        env={**os.environ, "PSModuleAnalysisCachePath": "NUL"},
    )


def _image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic metadata-only fixture; never executed")
    return path


def _check(directory: Path, processes: list[dict[str, str]], *, prefix: str = "") -> dict:
    payload = _quote(json.dumps(processes, ensure_ascii=False))
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". {_quote(SCRIPT)}; {prefix} "
        f"$items = @((ConvertFrom-Json -InputObject {payload})); "
        f"Get-ArgusInstallBlockers -Directory {_quote(directory)} -Processes $items "
        "| ConvertTo-Json -Compress"
    )
    result = _powershell(command)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_target_host_and_frozen_backend_block_replacement(tmp_path: Path) -> None:
    install = tmp_path / "installed"
    host = _image(install / "Argus.exe")
    backend = _image(install / "argus-backend/argus-backend.exe")
    result = _check(install, [
        {"ProcessName": "Argus", "Path": str(host)},
        {"ProcessName": "argus-backend", "Path": str(backend)},
    ])
    assert result == {"Blocking": 2, "Unknown": 0}


def test_other_installation_and_similar_path_are_not_owned(tmp_path: Path) -> None:
    install = tmp_path / "installed"
    install.mkdir()
    preview = _image(tmp_path / "preview/Argus.exe")
    similar = _image(tmp_path / "installed-other/argus-backend/argus-backend.exe")
    result = _check(install, [
        {"ProcessName": "Argus", "Path": str(preview)},
        {"ProcessName": "argus-backend", "Path": str(similar)},
    ])
    assert result == {"Blocking": 0, "Unknown": 0}


def test_inaccessible_identity_blocks_instead_of_authorizing_termination(tmp_path: Path) -> None:
    result = _check(tmp_path, [{"ProcessName": "Argus", "Path": ""}])
    assert result == {"Blocking": 0, "Unknown": 1}


def test_disappearing_image_is_unknown_not_an_unowned_process(tmp_path: Path) -> None:
    result = _check(tmp_path, [{"ProcessName": "Argus", "Path": str(tmp_path / "missing/Argus.exe")}])
    assert result == {"Blocking": 0, "Unknown": 1}


def test_unicode_quotes_and_shell_characters_remain_literal_paths(tmp_path: Path) -> None:
    install = tmp_path / "资料 ' $() & installation"
    host = _image(install / "Argus.exe")
    result = _check(install, [{"ProcessName": "Argus", "Path": str(host).upper()}])
    assert result == {"Blocking": 1, "Unknown": 0}


def test_junction_alias_resolves_to_the_same_installation(tmp_path: Path) -> None:
    install = tmp_path / "actual"
    _image(install / "Argus.exe")
    alias = tmp_path / "alias"
    prefix = f"New-Item -ItemType Junction -Path {_quote(alias)} -Target {_quote(install)} | Out-Null;"
    result = _check(install, [{"ProcessName": "Argus", "Path": str(alias / "Argus.exe")}], prefix=prefix)
    assert result == {"Blocking": 1, "Unknown": 0}


def test_empty_snapshot_is_idle_and_legacy_layout_is_checked(tmp_path: Path) -> None:
    assert _check(tmp_path, []) == {"Blocking": 0, "Unknown": 0}
    backend = _image(tmp_path / "resources/argus-backend/argus-backend.exe")
    assert _check(tmp_path, [{"ProcessName": "argus-backend", "Path": str(backend)}]) == {
        "Blocking": 1, "Unknown": 0,
    }


def test_installer_policy_contains_no_process_termination_or_command_evaluation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    hook = (ROOT / "desktop-tauri/src-tauri/installer-hooks.nsh").read_text(encoding="utf-8")
    for prohibited in ("Stop-Process", "TerminateProcess", "taskkill", "Invoke-Expression", ".Kill("):
        assert prohibited not in source
        assert prohibited not in hook
    assert "!macroundef CheckIfAppIsRunning" in hook
    assert "KillProcess" not in hook
    assert '-InstallDirectory "$INSTDIR"' in hook
    assert "IfSilent argus_check_abort_" in hook


def test_updater_verifies_download_before_stopping_only_its_supervisor() -> None:
    helper = (ROOT / "desktop-tauri/src-tauri/src/update_install.rs").read_text(encoding="utf-8")
    assert helper.index("let bytes = verified_download.await?") < helper.index("stop_owned_backend.await")
    assert helper.index("stop_owned_backend.await") < helper.index("install(bytes)")
    updater = (ROOT / "desktop-tauri/src-tauri/src/updater.rs").read_text(encoding="utf-8")
    assert "install_verified_update(" in updater
    assert "supervisor.stop().await" in updater
    assert "download_and_install(" not in updater
