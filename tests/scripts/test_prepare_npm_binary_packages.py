from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.prepare_npm_binary_packages import platform_version, validate_version


def test_beta_version_validation() -> None:
    version = "0.1.1-beta.g0123456789ab"
    assert validate_version(version) == version
    with pytest.raises(ValueError):
        validate_version("0.1.1")
    with pytest.raises(ValueError):
        validate_version("0.1.1-beta.latest")
    assert platform_version(version, "linux", "x64") == (
        "0.1.1-beta.g0123456789ab-linux-x64"
    )


def test_prepared_packages_use_argus_brand_and_exact_allowlist(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    binary = tmp_path / "argus-core"
    binary.write_bytes(b"frozen-argus")
    notices = tmp_path / "THIRD_PARTY_NOTICES.txt"
    notices.write_text("third-party notices\n", encoding="utf-8")
    output = tmp_path / "npm"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_npm_binary_packages.py",
            "--binary",
            str(binary),
            "--platform",
            "linux",
            "--arch",
            "x64",
            "--notices",
            str(notices),
            "--version",
            "0.1.1-beta.g0123456789ab",
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "@argusevolve" not in result.stdout  # paths stay implementation-neutral
    subprocess.run(
        [sys.executable, "scripts/verify_npm_binary_packages.py", str(output)],
        cwd=root,
        check=True,
    )

    main = json.loads((output / "argus" / "package.json").read_text())
    platform = json.loads(
        (output / "argus-linux-x64" / "package.json").read_text()
    )
    assert main["name"] == "@argusevolve/argus"
    assert platform["name"] == "@argusevolve/argus"
    assert platform["version"] == "0.1.1-beta.g0123456789ab-linux-x64"
    assert main["optionalDependencies"]["@argusevolve/argus-linux-x64"] == (
        "npm:@argusevolve/argus@0.1.1-beta.g0123456789ab-linux-x64"
    )
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert (
        output / "argus-linux-x64" / "bin" / "argus-core.sha256"
    ).read_text().strip() == f"{digest}  argus-core"
