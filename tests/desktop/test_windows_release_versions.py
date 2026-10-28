"""Metadata and installer gates specific to the Windows release candidate."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    assert "commit 59585e1aac2d2e3503aa1caececf3568dce51a47" in source
    assert (ROOT / "desktop-tauri/src-tauri/installer-template.LICENSE").is_file()


def test_installer_source_and_license_participate_in_release_identity(tmp_path):
    from argus.release import source_files

    root = tmp_path / "desktop-tauri/src-tauri"
    root.mkdir(parents=True)
    (root / "installer-template.nsi").write_text("synthetic installer input\n", encoding="utf-8")
    (root / "installer-template.LICENSE").write_text("synthetic license input\n", encoding="utf-8")
    assert set(source_files(tmp_path)) == {
        "desktop-tauri/src-tauri/installer-template.nsi",
        "desktop-tauri/src-tauri/installer-template.LICENSE",
    }
