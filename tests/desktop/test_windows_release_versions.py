"""Metadata and installer gates specific to the Windows release candidate."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "desktop-tauri/scripts/build-windows-backend.py"
    spec = importlib.util.spec_from_file_location("windows_release_versions_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_release_version_matrix_has_no_drift():
    version = _load().assert_release_versions(ROOT)
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)


@pytest.mark.parametrize("metadata", [
    "desktop-tauri/src-tauri/tauri.conf.json",
    "plugins/argus/.claude-plugin/plugin.json",
    "plugins/argus/.codex-plugin/plugin.json",
])
def test_version_matrix_rejects_divergent_host_or_plugin_metadata(tmp_path, metadata):
    # Copy only named public release metadata, never a profile or signing file.
    files = (
        "pyproject.toml", "argus/__init__.py", "uv.lock",
        "frontend/core/package.json", "frontend/web/package.json", "frontend/web/package-lock.json",
        "frontend/tui/package.json", "frontend/tui/package-lock.json", "desktop-tauri/package.json",
        "desktop-tauri/package-lock.json", "desktop-tauri/src-tauri/Cargo.toml", "desktop-tauri/src-tauri/Cargo.lock",
        "desktop-tauri/src-tauri/tauri.conf.json", ".claude-plugin/marketplace.json",
        "plugins/argus/.claude-plugin/plugin.json", "plugins/argus/.codex-plugin/plugin.json",
    )
    for filename in files:
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / filename).read_bytes())
    config_file = tmp_path / metadata
    config = json.loads(config_file.read_text(encoding="utf-8"))
    config["version"] = "999.0.0"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RuntimeError, match="version drift"):
        _load().assert_release_versions(tmp_path)


def test_windows_template_cannot_launch_legacy_uninstallers_or_delete_shared_bootstrappers():
    config = json.loads((ROOT / "desktop-tauri/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    windows = config["bundle"]["windows"]
    assert windows["allowDowngrades"] is False
    assert windows["nsis"]["template"] == "installer-template.nsi"
    source = (ROOT / "desktop-tauri/src-tauri/installer-template.nsi").read_text(encoding="utf-8")
    assert "Function PageLeaveReinstall" not in source
    assert "reinst_uninstall" not in source
    assert "msiexec" not in source
    assert "ExecWait '$R1'" not in source
    assert '$TEMP\\MicrosoftEdge' not in source
    assert 'Delete "$INSTDIR\\$OldMainBinaryName"' not in source
    early = source.split("Section EarlyChecks", 1)[1].split("SectionEnd", 1)[0]
    assert "SemverCompare" in early
    assert "SetErrorLevel 2" in early
    assert "CheckIfAppIsRunning" in early
    assert "${Silent}" not in early  # The version guard must cover every mode.
    assert source.count('!insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe"') == 3
    package = json.loads((ROOT / "desktop-tauri/package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["@tauri-apps/cli"] == "2.11.4"
    assert "20f4ecc730defb71f1342eaeaec4021df13be3d843abba0effe88ea5835fa079" in source
    assert (ROOT / "desktop-tauri/src-tauri/installer-template.LICENSE").is_file()


def test_installer_source_and_license_participate_in_release_identity(tmp_path):
    from argus.release import compute_source_digest

    root = tmp_path / "desktop-tauri/src-tauri"
    root.mkdir(parents=True)
    before = compute_source_digest(tmp_path)
    (root / "installer-template.nsi").write_text("synthetic installer input\n", encoding="utf-8")
    with_template = compute_source_digest(tmp_path)
    assert with_template != before
    (root / "installer-template.LICENSE").write_text("synthetic license input\n", encoding="utf-8")
    assert compute_source_digest(tmp_path) != with_template
