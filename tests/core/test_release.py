from __future__ import annotations

import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from argus_skill.core import runtime_identity as runtime_identity_module
from argus_skill.maintenance import deploy_boundary
from argus_skill.maintenance.deploy_boundary import (
    ReviewedChange,
    approve_reviewed_change,
    deploy_reviewed_change,
    deployment_input_digest,
)
from argus_skill.release import (
    MANIFEST_SCHEMA_VERSION,
    _source_files,
    compute_source_digest,
    release_identity,
    release_manifest,
)


def test_release_digest_covers_runtime_and_frontend_build_inputs() -> None:
    root = Path(__file__).parents[2]
    included = {
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in _source_files(root)
    }

    assert {
        "argus_skill/verticals/classical_poetry/sources.yaml",
        "argus_skill/verticals/chip_design/references/workflow.md",
        "frontend/tui/scripts/build-bundle.mjs",
        "frontend/web/src/index.css",
        "frontend/web/public/manifest.webmanifest",
        "frontend/web/package-lock.json",
        "frontend/web/vite.config.ts",
        "frontend/web/index.html",
        "argus_skill/desktop_backend_entry.py",
        "desktop-tauri/argus_backend.spec",
        "desktop-tauri/src-tauri/src/backend.rs",
        "desktop-tauri/src-tauri/tauri.conf.json",
        "desktop-tauri/src-tauri/installer-hooks.nsh",
        "desktop-tauri/scripts/stage-release.ps1",
        "desktop-tauri/scripts/smoke-host.py",
        "desktop-tauri/resources/argus-backend/.gitkeep",
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

    The digest it carries is only refreshed when a release is built, so
    comparing it against the working tree on an ordinary commit asserts that
    every commit is a release. That check belongs to the release build and
    lives in the test below; this one still catches a corrupt, hand-edited, or
    schema-drifted manifest at any time.
    """
    manifest = release_manifest()
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["release_id"] == (
        f"{manifest['package_version']}+{manifest['source_digest'][:16]}"
    )


@pytest.mark.skipif(
    not os.environ.get("ARGUS_RELEASE_BUILD"),
    reason="the shipped digest is refreshed by the release build, not by each commit",
)
def test_release_manifest_matches_current_shipped_source() -> None:
    root = Path(__file__).parents[2]
    manifest = release_manifest()
    assert manifest["source_digest"] == compute_source_digest(root)
    identity = release_identity(root)
    assert identity["release_matches_source"] is True
    assert identity["runtime_source_digest"] == manifest["source_digest"]


def test_checked_in_frontend_contract_matches_current_release() -> None:
    root = Path(__file__).parents[2]
    manifest = release_manifest()
    generated = (root / "frontend/core/src/release.generated.ts").read_text(
        encoding="utf-8"
    )
    assert manifest["release_id"] in generated
    assert manifest["source_digest"] in generated

    tui = (root / "frontend/tui/bundle/argus.mjs").read_text(encoding="utf-8")
    assert manifest["release_id"] in tui

    web_root = root / "frontend/web/dist"
    index = (web_root / "index.html").read_text(encoding="utf-8")
    assets = [
        web_root / ref.lstrip("/")
        for ref in re.findall(r'(?:src|href)="([^"]+\.js)"', index)
    ]
    assert assets
    assert any(
        manifest["release_id"] in path.read_text(encoding="utf-8")
        for path in assets
    )


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


def test_installed_frontend_dependencies_do_not_change_release_identity() -> None:
    root = Path(__file__).parents[2]
    node_modules = root / "frontend" / "web" / "node_modules"
    dependency = node_modules / "_release-test" / "package.json"
    had_node_modules = node_modules.exists()
    before = compute_source_digest(root)
    try:
        dependency.parent.mkdir(parents=True, exist_ok=True)
        dependency.write_text('{"name": "ignored"}\n', encoding="utf-8")
        assert compute_source_digest(root) == before
    finally:
        dependency.unlink(missing_ok=True)
        dependency.parent.rmdir()
        if not had_node_modules:
            node_modules.rmdir()


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


def test_deployment_boundary_uses_tauri_desktop_verification() -> None:
    commands = deploy_boundary._commands(("desktop-tauri/src/main.ts",))
    rendered = [" ".join(command) for command in commands]

    assert any("--prefix desktop-tauri ci" in command for command in rendered)
    assert any("cargo check --manifest-path desktop-tauri" in command for command in rendered)
    assert any("desktop-tauri/scripts/build-backend.ps1" in command for command in rendered)
    assert any("--prefix desktop-tauri run build:unsigned" in command for command in rendered)
    assert not any("electron-builder" in command for command in rendered)
    assert not any("--prefix desktop ci" in command for command in rendered)


def _deployment_repo(root: Path) -> tuple[Path, Path, Path, str, str]:
    public = root / "public.git"
    private = root / "private.git"
    repo = root / "author"
    for bare in (public, private):
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "argus_skill" / "release_tools").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "argus_skill" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "argus_skill" / "feature.py").write_text("VALUE = 'old'\n", encoding="utf-8")
    (repo / "argus_skill" / "release.py").write_text(
        "def release_identity(root=None):\n"
        "    return {'release_matches_source': True, 'release_id': 'test-release'}\n",
        encoding="utf-8",
    )
    (repo / "argus_skill" / "release_tools" / "build_release.py").write_text(
        "from pathlib import Path\n"
        "Path('argus_skill/release-artifact.txt').write_text('built\\n')\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_gate.py").write_text(
        "def test_repository_gate():\n    assert True\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='deploy-test'\nversion='1'\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(public)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "private", str(private)], check=True)
    for remote in ("origin", "private"):
        subprocess.run(["git", "-C", str(repo), "push", remote, "main"], check=True, capture_output=True)
    (repo / "argus_skill" / "feature.py").write_text("VALUE = 'new'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "reviewed fix"], check=True, capture_output=True)
    candidate = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, public, private, base, candidate


def _reviewed_change(
    repo: Path,
    base: str,
    candidate: str,
    receipts: Path,
) -> ReviewedChange:
    return ReviewedChange(
        repository=repo,
        public_base=base,
        reviewed_candidate=candidate,
        reviewer_verdict="done",
        acceptance_command=(
            "python", "-c",
            "from argus_skill.feature import VALUE; assert VALUE == 'new'",
        ),
        evidence_refs=("events.jsonl: observed framework failure",),
        mission_id="maintenance-mission",
        receipt_dir=receipts,
    )


def _approval(change: ReviewedChange):
    return approve_reviewed_change(change, {
        "id": "decision-maintenance-mission",
        "input_digest": deployment_input_digest(change),
        "item_id": change.mission_id,
        "status": "resolved",
        "selected_option": "adopt",
    })


def test_deployment_boundary_rejects_regression_mismatch_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, public, _private, base, candidate = _deployment_repo(tmp_path)
    change = _reviewed_change(repo, base, candidate, tmp_path / "receipts")
    loaded = tmp_path / "loaded-runtime"
    loaded.write_text("still running old release\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed input"):
        approve_reviewed_change(change, {
            "id": "decision-maintenance-mission",
            "item_id": change.mission_id,
            "status": "resolved",
            "selected_option": "adopt",
        })

    stale_approval = approve_reviewed_change(change, {
        "id": "decision-maintenance-mission",
        "input_digest": "stale-reviewed-input",
        "item_id": change.mission_id,
        "status": "resolved",
        "selected_option": "adopt",
    })
    stale = deploy_reviewed_change(change, stale_approval)
    assert stale["verdict"] == "REJECT"
    assert stale["approval_matches_input"] is False
    assert stale["failure_stage"] == "approval"
    assert list((tmp_path / "receipts").glob("deployment-*.json"))

    approval = _approval(change)
    mismatch = deploy_reviewed_change(
        replace(change, evidence_refs=("different evidence",)),
        approval,
    )
    assert mismatch["verdict"] == "REJECT"
    assert deploy_reviewed_change(change, approval)["verdict"] == "REJECT"

    invalid = deploy_reviewed_change(
        replace(change, public_base="missing"),
        _approval(change),
    )
    assert invalid["baseline_failures"] == []
    assert invalid["candidate_failures"] == []

    restarted_approval = _approval(change)
    process = deploy_boundary._PROCESS
    monkeypatch.setattr(deploy_boundary, "_PROCESS", object())
    restarted = deploy_reviewed_change(change, restarted_approval)
    assert restarted["verdict"] == "REJECT"
    monkeypatch.setattr(deploy_boundary, "_PROCESS", process)

    (repo / "tests" / "test_regression.py").write_text(
        "def test_new_regression():\n    assert False\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "bad candidate"], check=True, capture_output=True)
    bad_candidate = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bad = replace(change, reviewed_candidate=bad_candidate)
    regression = deploy_reviewed_change(bad, _approval(bad))

    assert regression["verdict"] == "REJECT"
    assert regression["failure_subset"] is False
    assert loaded.read_text() == "still running old release\n"
    public_main = subprocess.run(
        ["git", "--git-dir", str(public), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert public_main == base


@pytest.mark.skipif(
    os.name == "nt",
    reason="bare-repository partial-publication hook semantics are covered on POSIX",
)
def test_deployment_boundary_publishes_both_routes_before_roll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, public, private, base, candidate = _deployment_repo(tmp_path)
    change = _reviewed_change(repo, base, candidate, tmp_path / "receipts")
    real_git = deploy_boundary._git

    def reject_private_main(repo_path, *args, check=True):
        if (
            len(args) >= 3
            and args[0] == "push"
            and Path(args[1]).resolve() == private.resolve()
            and str(args[-1]).endswith(":refs/heads/main")
        ):
            raise subprocess.CalledProcessError(1, ["git", *args])
        return real_git(repo_path, *args, check=check)

    monkeypatch.setattr(deploy_boundary, "_git", reject_private_main)
    partial = deploy_reviewed_change(change, _approval(change))

    assert partial["verdict"] == "REJECT"
    assert partial["partial_publication"] is True
    assert partial["public_sync_published"] is True
    assert partial["private_sync_published"] is True
    assert partial["public_main_updated"] is True
    assert partial["private_main_updated"] is False
    assert partial["daemon_roll_permitted"] is False
    assert partial["runtime_source_root"] == ""

    monkeypatch.setattr(deploy_boundary, "_git", real_git)
    # Date-scoped sync branches from the partial run are absent the next day.
    for bare, branch in (
        (public, partial["public_sync_branch"]),
        (private, partial["private_sync_branch"]),
    ):
        subprocess.run(
            ["git", "--git-dir", str(bare), "update-ref", "-d", f"refs/heads/{branch}"],
            check=True,
        )
    receipt = deploy_reviewed_change(change, _approval(change))

    assert receipt["verdict"] == "ADOPT"
    assert receipt["release_matches_source"] is True
    assert receipt["acceptance_reproduced"] is True
    assert receipt["repository_parity_verified"] is True
    assert receipt["input_digest"] and receipt["run_digest"]
    assert receipt["decision_id"] == "decision-maintenance-mission"
    assert receipt["public_main_updated"] is True
    assert receipt["public_sync_published"] is True
    assert receipt["private_sync_published"] is True
    assert receipt["private_main_updated"] is True
    assert receipt["both_publication_routes_complete"] is True
    assert receipt["daemon_roll_permitted"] is True
    assert Path(receipt["runtime_source_root"]).is_dir()
    for bare, branch in (
        (public, receipt["public_sync_branch"]),
        (private, receipt["private_sync_branch"]),
    ):
        main = subprocess.run(
            ["git", "--git-dir", str(bare), "rev-parse", "main"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sync = subprocess.run(
            ["git", "--git-dir", str(bare), "rev-parse", branch],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert main == sync
    assert list((tmp_path / "receipts").glob("deployment-*.json"))
