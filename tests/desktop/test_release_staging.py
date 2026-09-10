from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.mark.parametrize("machine,bundle_arch,updater_arch", [
    ("arm64", "aarch64", "aarch64"),
    ("x86_64", "x64", "x86_64"),
])
def test_macos_staging_maps_tauri_names_to_updater_targets(
    tmp_path, monkeypatch, machine, bundle_arch, updater_arch,
):
    script = Path(__file__).resolve().parents[2] / "desktop-tauri/scripts/stage-macos-release.py"
    spec = importlib.util.spec_from_file_location("macos_release_staging", script)
    staging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staging)
    monkeypatch.setattr(staging, "ROOT", tmp_path)
    monkeypatch.setattr(staging.platform, "machine", lambda: machine)
    (tmp_path / "package.json").write_text(json.dumps({"version": "0.1.3"}))
    bundle = tmp_path / "src-tauri/target/release/bundle"
    (bundle / "dmg").mkdir(parents=True)
    (bundle / "macos").mkdir()
    (bundle / f"dmg/Argus_0.1.3_{bundle_arch}.dmg").write_bytes(b"native installer")
    (bundle / "macos/Argus.app.tar.gz").write_bytes(b"native update")
    (bundle / "macos/Argus.app.tar.gz.sig").write_text("update signature\n")

    staging.main()

    output = tmp_path / "release"
    assert (output / f"Argus-0.1.3-macos-{updater_arch}.dmg").read_bytes() == b"native installer"
    manifest = json.loads((output / f"latest-darwin-{updater_arch}.json").read_text())
    target = manifest["platforms"][f"darwin-{updater_arch}"]
    assert target["url"].endswith(f"/Argus-0.1.3-macos-{updater_arch}.app.tar.gz")
    assert target["signature"] == "update signature"


def test_linux_staging_requires_signed_appimage_and_keeps_debian_installer(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "desktop-tauri/scripts/stage-linux-release.py"
    spec = importlib.util.spec_from_file_location("linux_release_staging", script)
    staging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staging)
    monkeypatch.setattr(staging, "ROOT", tmp_path)
    (tmp_path / "package.json").write_text(json.dumps({"version": "0.1.5"}))
    bundle = tmp_path / "src-tauri/target/release/bundle"
    (bundle / "appimage").mkdir(parents=True)
    (bundle / "deb").mkdir()
    image = bundle / "appimage/Argus_0.1.5_amd64.AppImage"
    image.write_bytes(b"native Linux app")
    (bundle / "deb/Argus_0.1.5_amd64.deb").write_bytes(b"Debian installer")
    with pytest.raises(SystemExit, match="Signed Linux AppImage"):
        staging.main()
    image.with_name(image.name + ".sig").write_text("Linux signature\n")

    staging.main()

    output = tmp_path / "release"
    assert (output / "Argus-0.1.5-linux-x86_64.AppImage").read_bytes() == b"native Linux app"
    assert (output / "Argus-0.1.5-linux-x86_64.deb").read_bytes() == b"Debian installer"
    manifest = json.loads((output / "latest-linux-x86_64.json").read_text())
    target = manifest["platforms"]["linux-x86_64"]
    assert target["url"].endswith("/Argus-0.1.5-linux-x86_64.AppImage")
    assert target["signature"] == "Linux signature"


def test_release_notes_require_all_desktop_platforms(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "desktop-tauri/scripts/prepare-github-release.py"
    spec = importlib.util.spec_from_file_location("github_release_staging", script)
    staging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staging)
    targets = {
        "windows-x86_64": "Argus-0.1.5-setup.exe",
        "darwin-aarch64": "Argus-0.1.5-macos-aarch64.app.tar.gz",
        "darwin-x86_64": "Argus-0.1.5-macos-x86_64.app.tar.gz",
        "linux-x86_64": "Argus-0.1.5-linux-x86_64.AppImage",
    }
    for platform, name in targets.items():
        (tmp_path / name).write_bytes(b"x" * 1_000_001)
        (tmp_path / (name + ".sig")).write_text("fixture signature")
        manifest = {
            "version": "0.1.5",
            "platforms": {platform: {
                "url": f"https://github.com/lbx154/Argus/releases/download/v0.1.5/{name}",
                "signature": "fixture signature",
            }},
        }
        filename = "latest.json" if platform == "windows-x86_64" else f"latest-{platform}.json"
        (tmp_path / filename).write_text(json.dumps(manifest))
    for name in ("Argus-0.1.5-macos-aarch64.dmg", "Argus-0.1.5-macos-x86_64.dmg",
                 "Argus-0.1.5-linux-x86_64.deb"):
        (tmp_path / name).write_bytes(b"native installer")
    with zipfile.ZipFile(tmp_path / "argus_skill-0.1.5-py3-none-any.whl", "w") as wheel:
        wheel.writestr("argus_skill/release_manifest.json", json.dumps({
            "package_version": "0.1.5", "release_id": "0.1.5+test",
        }))
    notes = tmp_path.parent / "trial-release-notes.txt"
    monkeypatch.setattr(sys, "argv", [str(script), str(tmp_path), "--notes", str(notes)])

    staging.main()

    combined = json.loads((tmp_path / "latest.json").read_text())
    assert set(combined["platforms"]) == set(targets)
    assert "Linux x86_64" in notes.read_text()
    assert (tmp_path / "SHA256SUMS").is_file()
