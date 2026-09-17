"""Stage Linux x86_64 desktop packages and signed AppImage updater metadata."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = json.loads((ROOT / "package.json").read_text())["version"]
    bundle = ROOT / "src-tauri/target/release/bundle"
    images = list((bundle / "appimage").glob(f"*_{version}_*.AppImage"))
    debs = list((bundle / "deb").glob(f"*_{version}_*.deb"))
    if len(images) != 1 or len(debs) != 1:
        raise SystemExit("Expected one Linux AppImage and one Debian package")
    signature = images[0].with_name(images[0].name + ".sig")
    if not signature.is_file() or not signature.read_text().strip():
        raise SystemExit("Signed Linux AppImage is required")
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    name = f"Argus-{version}-linux-x86_64"
    for source, target in [
        (images[0], name + ".AppImage"),
        (signature, name + ".AppImage.sig"),
        (debs[0], name + ".deb"),
    ]:
        shutil.copy2(source, output / target)
    manifest = {
        "version": version,
        "notes": f"Argus {version}: private-key desktop trial.",
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "platforms": {"linux-x86_64": {
            "url": f"https://github.com/lbx154/Argus/releases/download/v{version}/{name}.AppImage",
            "signature": signature.read_text().strip(),
        }},
    }
    (output / "latest-linux-x86_64.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Staged Linux AppImage, Debian installer and signed updater")


if __name__ == "__main__":
    main()
