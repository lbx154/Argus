from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from argus_skill.release import (
    MANIFEST_SCHEMA_VERSION,
    compute_source_digest,
    release_identity,
    release_manifest,
)


def test_release_manifest_matches_current_shipped_source() -> None:
    root = Path(__file__).parents[2]
    manifest = release_manifest()
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["source_digest"] == compute_source_digest(root)
    assert manifest["release_id"] == (
        f"{manifest['package_version']}+{manifest['source_digest'][:16]}"
    )
    identity = release_identity(root)
    assert identity["release_matches_source"] is True
    assert identity["runtime_source_digest"] == manifest["source_digest"]


def test_release_generated_frontend_contract_is_current() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/generate_release_manifest.py", "--check"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
