from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from argus_skill.core import runtime_identity as runtime_identity_module
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


def test_release_manifest_cannot_be_refreshed_without_frontend_build() -> None:
    root = Path(__file__).parents[2]
    manifest_path = root / "argus_skill" / "release_manifest.json"
    generated_path = root / "frontend" / "core" / "src" / "release.generated.ts"
    before = (manifest_path.read_bytes(), generated_path.read_bytes())

    result = subprocess.run(
        [sys.executable, "scripts/generate_release_manifest.py"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "run scripts/build_release.py" in result.stderr
    assert (manifest_path.read_bytes(), generated_path.read_bytes()) == before


def test_checked_in_frontend_artifacts_match_current_release() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/check_release_artifacts.py"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_untracked_runtime_skill_does_not_change_release_identity() -> None:
    root = Path(__file__).parents[2]
    generated = root / "argus_skill" / "builtin_skills" / "_release-test-untracked.md"
    before = compute_source_digest(root)
    try:
        generated.write_text("# Runtime-generated skill\n", encoding="utf-8")
        assert compute_source_digest(root) == before
    finally:
        generated.unlink(missing_ok=True)


def test_untracked_new_source_participates_before_first_commit() -> None:
    root = Path(__file__).parents[2]
    source = root / "argus_skill" / "_release_test_untracked_source.py"
    before = compute_source_digest(root)
    try:
        source.write_text("VALUE = 1\n", encoding="utf-8")
        assert compute_source_digest(root) != before
    finally:
        source.unlink(missing_ok=True)


def test_strict_release_preflight_rejects_manifest_source_mismatch(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", "1")
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: {"release_matches_source": False},
    )

    error = runtime_identity_module.release_match_preflight_error()

    assert "does not match" in error
    assert "build_release.py" in error


def test_release_preflight_is_permissive_unless_enabled(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", raising=False)
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: {"release_matches_source": False},
    )

    assert runtime_identity_module.release_match_preflight_error() == ""
