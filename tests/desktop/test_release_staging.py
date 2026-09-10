from __future__ import annotations

import importlib.util
import json
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
