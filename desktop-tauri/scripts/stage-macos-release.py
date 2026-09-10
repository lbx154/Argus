"""Stage architecture-specific macOS installers and signed updater metadata."""
from __future__ import annotations

import json
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = json.loads((ROOT / "package.json").read_text())["version"]
    arch = {"arm64": "aarch64", "x86_64": "x86_64"}[platform.machine()]
    bundle_arch = "x64" if arch == "x86_64" else arch
    bundle = ROOT / "src-tauri/target/release/bundle"
    installers = list((bundle / "dmg").glob(f"Argus_{version}_{bundle_arch}.dmg"))
    if len(installers) != 1:
        raise SystemExit(f"Expected one macOS {arch} installer for {version}")
    archive = bundle / "macos/Argus.app.tar.gz"
    signature = archive.with_name(archive.name + ".sig")
    if not archive.is_file() or not signature.is_file() or not signature.read_text().strip():
        raise SystemExit("Signed macOS updater archive is required")
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    name = f"Argus-{version}-macos-{arch}"
    for source, target in [(installers[0], name + ".dmg"),
                           (archive, name + ".app.tar.gz"),
                           (signature, name + ".app.tar.gz.sig")]:
        shutil.copy2(source, output / target)
    manifest = {
        "version": version,
        "notes": f"Argus {version}: private-key desktop trial.",
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "platforms": {f"darwin-{arch}": {
            "url": f"https://github.com/lbx154/Argus/releases/download/v{version}/{name}.app.tar.gz",
            "signature": signature.read_text().strip(),
        }},
    }
    (output / f"latest-darwin-{arch}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Staged macOS {arch} installer and signed updater")


if __name__ == "__main__":
    main()
