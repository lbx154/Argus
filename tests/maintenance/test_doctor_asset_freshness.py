"""Doctor warns when a committed frontend bundle predates its source."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from argus.maintenance.doctor import DoctorContext, _checkout_finding

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

# Git rejects tiny epochs as dates, so anchor the fake history in 2023.
BASE = 1_700_000_000


def _git(repo: Path, *argv: str, when: int) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000",
    }
    subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=True, capture_output=True, text=True, env=env,
    )


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    (repo / "argus").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='argus'\n", encoding="utf-8")
    (repo / "argus" / "release_manifest.json").write_text("{}", encoding="utf-8")
    for rel in ("frontend/web/src", "frontend/web/dist", "frontend/core/src"):
        (repo / rel).mkdir(parents=True)
    (repo / "frontend/web/dist/index.html").write_text("<html></html>", encoding="utf-8")
    (repo / "frontend/web/src/app.tsx").write_text("export const a = 1;\n", encoding="utf-8")
    (repo / "frontend/core/src/shared.ts").write_text("export const c = 1;\n", encoding="utf-8")
    _git(repo, "init", "-q", when=BASE)
    _git(repo, "add", ".", when=BASE)
    _git(repo, "commit", "-q", "-m", "bundles and source together", when=BASE)
    return repo


def _context(tmp_path: Path, repo: Path) -> DoctorContext:
    return DoctorContext(
        global_root=tmp_path / "global", project_root=tmp_path / "project", checkout=repo,
    )


def _freshness(findings) -> object:
    matches = [item for item in findings if item.code == "ARGUS-ASSET-002"]
    assert len(matches) == 1, [item.code for item in findings]
    return matches[0]


def test_shared_core_source_counts_for_web_bundle(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    (repo / "frontend/core/src/shared.ts").write_text("export const c = 2;\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "change shared frontend code", when=BASE + 3_600)
    finding = _freshness(_checkout_finding(_context(tmp_path, repo)))
    assert finding.status == "assets_stale"
    assert "Web bundle" in finding.detail


def test_a_tree_without_history_gets_no_verdict(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    (repo / "argus").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='argus'\n", encoding="utf-8")
    (repo / "argus" / "release_manifest.json").write_text("{}", encoding="utf-8")
    (repo / "frontend/web/dist").mkdir(parents=True)
    (repo / "frontend/web/dist/index.html").write_text("<html></html>", encoding="utf-8")
    codes = [item.code for item in _checkout_finding(_context(tmp_path, repo))]
    assert "ARGUS-ASSET-001" in codes
    assert "ARGUS-ASSET-002" not in codes
