"""Build optional plugin wheels and their checksum catalog, separately from Argus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    destination = ROOT / "dist-plugins"
    destination.mkdir(exist_ok=True)
    catalog_path = ROOT / "argus_skill/plugin_catalog.json"
    previous = (
        json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog_path.exists()
        else {"plugins": []}
    )
    # External releases are maintained independently of this checkout. A host
    # build must not erase them just because their private source is absent.
    catalog = {entry["id"]: entry for entry in previous["plugins"]}
    built = set()
    for path in sorted((ROOT / "argus_skill/verticals").glob("*/workbench.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        # `build` is a release-only dependency; build isolation supplies the
        # plugin's backend. This does not install a plugin on the build host.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(destination),
                str(path.parent),
            ],
            check=True,
        )
        wheels = list(destination.glob(f"{spec['package']}-{spec['version']}-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected one platform-independent plugin wheel")
        wheel = wheels[0]
        spec.pop("frontend", None)
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        repository = os.environ.get("GITHUB_REPOSITORY", "lbx154/Argus")
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if not tag.startswith("v"):
            tag = "v" + version
        release_base = os.environ.get(
            "ARGUS_PLUGIN_RELEASE_BASE_URL",
            f"https://github.com/{repository}/releases/download/{tag}",
        ).rstrip("/")
        spec["artifact"] = {
            "filename": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "url": f"{release_base}/{wheel.name}" if release_base else "",
        }
        catalog[spec["id"]] = spec
        built.add(spec["id"])
    catalog = [catalog[name] for name in sorted(catalog)]
    for spec in catalog:
        artifact = spec.get("artifact", {})
        artifact.pop("local", None)
        if not str(artifact.get("url", "")).startswith("https://") or not re.fullmatch(
            r"[a-f0-9]{64}", artifact.get("sha256", "")
        ):
            raise ValueError(f"Plugin {spec['id']} needs an HTTPS release URL and SHA-256")
    # The shipped catalog never embeds paths to the maintainer's computer.
    (ROOT / "argus_skill/plugin_catalog.json").write_text(
        json.dumps({"plugins": catalog}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for spec in catalog:
        if spec["id"] in built:
            spec["artifact"]["local"] = spec["artifact"]["filename"]
    (destination / "catalog.json").write_text(
        json.dumps({"plugins": catalog}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Built {len(built)} optional packages; retained {len(catalog) - len(built)} external releases"
    )


if __name__ == "__main__":
    main()
