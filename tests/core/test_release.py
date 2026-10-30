from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from argus.core import runtime_identity as runtime_identity_module
from argus.release import (
    MANIFEST_SCHEMA_VERSION,
    _source_files,
    release_identity,
    release_manifest,
    source_files,
)


def test_release_files_cover_runtime_and_frontend_build_inputs() -> None:
    root = Path(__file__).parents[2]
    included = {
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in _source_files(root)
    }

    assert {
        "argus/core/timeline_example.json",
        "frontend/tui/scripts/build-bundle.mjs",
        "frontend/web/src/index.css",
        "frontend/web/public/manifest.webmanifest",
        "frontend/web/package-lock.json",
        "frontend/web/vite.config.ts",
        "frontend/web/index.html",
        "desktop-tauri/src-tauri/src/backend.rs",
        "desktop-tauri/src-tauri/tauri.conf.json",
        "desktop-tauri/src-tauri/installer-hooks.nsh",
        "desktop-tauri/scripts/stage-release.ps1",
        "desktop-tauri/package-lock.json",
        "argus_doctor.py",
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
        "plugins/argus/.codex-plugin/plugin.json",
        "plugins/argus/.claude-plugin/plugin.json",
        "plugins/argus/skills/argus-run/SKILL.md",
        "plugins/argus/bin/argus-plugin-mcp",
        "plugins/argus/install.sh",
        "plugins/argus/install.ps1",
    }.issubset(included)
    assert "frontend/web/dist/index.html" not in included
    assert "frontend/tui/bundle/argus.mjs" not in included


def test_release_manifest_is_internally_consistent() -> None:
    """Always-on: the checked-in manifest must be well formed.

    The file list it carries is only refreshed when a release is built, so
    comparing it against the working tree on an ordinary commit asserts that
    every commit is a release. That check belongs to the release build and
    lives in the test below; this one still catches a corrupt, hand-edited, or
    schema-drifted manifest at any time.
    """
    manifest = release_manifest()
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["release_id"] == (
        f"{manifest['package_version']}+{manifest['git_commit'][:12]}"
    )
    assert manifest["source_files"]


@pytest.mark.skipif(
    not os.environ.get("ARGUS_RELEASE_BUILD"),
    reason="the shipped file list is refreshed by the release build, not by each commit",
)
def test_release_manifest_matches_current_shipped_source() -> None:
    root = Path(__file__).parents[2]
    manifest = release_manifest()
    assert manifest["source_files"] == source_files(root)
    identity = release_identity(root)
    assert identity["release_matches_source"] is True
    assert identity["runtime_source_digest"].startswith(identity["manifest_source_digest"])


def test_bundled_workbench_identity_tracks_source_not_dependencies(tmp_path):
    vertical = tmp_path / "argus" / "verticals" / "sample"
    vertical.mkdir(parents=True)
    source = vertical / "tools.mjs"
    source.write_text("export const version = 1;")
    for directory in ("node_modules/pkg", "dist", "__pycache__"):
        generated = vertical / directory / "metadata.json"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text('{"local": true}')
    assert list(source_files(tmp_path)) == ["argus/verticals/sample/tools.mjs"]


@pytest.fixture
def release_checkout(tmp_path: Path) -> Path:
    """Tracking-policy tests own their checkout, never mutate the live source.

    They must work when this suite itself runs from an exported/no-Git build
    directory, and must not race parallel release builds through fixed files.
    """
    subprocess.run(["git", "init", "--quiet", "--template=", str(tmp_path)], check=True)
    runtime = tmp_path / "argus" / "runtime.py"
    runtime.parent.mkdir()
    runtime.write_text("VALUE = 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "argus/runtime.py"], check=True)
    return tmp_path


def test_untracked_runtime_skill_does_not_change_release_identity(release_checkout: Path) -> None:
    root = release_checkout
    generated = root / "argus" / "builtin_skills" / "_release-test-untracked.md"
    generated.parent.mkdir()
    generated.write_text("# Runtime-generated skill\n", encoding="utf-8")
    assert "argus/builtin_skills/_release-test-untracked.md" not in source_files(root)


def test_untracked_new_source_participates_before_first_commit(release_checkout: Path) -> None:
    root = release_checkout
    source = root / "argus" / "_release_test_untracked_source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    assert "argus/_release_test_untracked_source.py" in source_files(root)


def test_installed_frontend_dependencies_do_not_change_release_identity(release_checkout: Path) -> None:
    root = release_checkout
    dependency = root / "frontend" / "web" / "node_modules" / "_release-test" / "package.json"
    dependency.parent.mkdir(parents=True)
    dependency.write_text('{"name": "ignored"}\n', encoding="utf-8")
    assert list(source_files(root)) == ["argus/runtime.py"]


def test_repository_parity_tool_does_not_change_product_release_identity(tmp_path: Path) -> None:
    runtime = tmp_path / "argus" / "runtime.py"
    runtime.parent.mkdir()
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    checker = runtime.parent / "release_tools" / "check_repository_parity.py"
    checker.parent.mkdir()
    checker.write_text("PRIVATE_ONLY_PATTERNS = ('private-notes.md',)\n", encoding="utf-8")
    assert list(source_files(tmp_path)) == ["argus/runtime.py"]


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
    assert "pip install -e ." in error


def test_release_preflight_is_permissive_unless_enabled(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", raising=False)
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: {"release_matches_source": False},
    )

    assert runtime_identity_module.release_match_preflight_error() == ""


def test_windows_release_checks_live_in_explicit_ci_workflows() -> None:
    """Dev publishes reviewed commits without replaying the old dual-remote gate.

    Publication safety is covered by tests/maintenance/test_publication.py.
    Desktop build verification remains in the explicit CI workflows, not an
    import of the removed maintenance.deploy_boundary module.
    """
    root = Path(__file__).parents[2]
    desktop = yaml.safe_load((root / ".github/workflows/desktop.yml").read_text(encoding="utf-8"))
    commands = "\n".join(step.get("run", "") for step in desktop["jobs"]["desktop"]["steps"])
    assert "npm --prefix desktop-tauri ci" in commands
    assert "npm --prefix desktop-tauri run test:release" in commands
    assert "cargo test --manifest-path desktop-tauri" in commands
    assert "npm --prefix desktop-tauri run build:unsigned" in commands
    assert "electron-builder" not in commands

    workflow = yaml.safe_load((root / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["desktop"]["steps"]
    names = [step.get("name", "") for step in steps]
    assert names.index("Verify Windows staging and signature boundary") < names.index("Build signed desktop updates")
    assert any("stage-release.ps1" in step.get("run", "") for step in steps)
