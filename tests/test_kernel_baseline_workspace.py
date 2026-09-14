from __future__ import annotations

import subprocess
from pathlib import Path

from argus.verticals.kernel_engineering.baseline_workspace import (
    prepare_baseline_workspace,
)


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "kernel.py").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "kernel.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=path, check=True)
    return path


def test_clean_reference_does_not_revert_candidate(tmp_path: Path) -> None:
    project = _repo(tmp_path / "project")
    (project / "kernel.py").write_text("candidate\n", encoding="utf-8")

    workspace = prepare_baseline_workspace(project, tmp_path / "state")

    assert workspace is not None
    assert workspace.candidate_dirty is True
    assert workspace.changed_paths == ("kernel.py",)
    assert (workspace.reference_root / "kernel.py").read_text() == "baseline\n"
    assert (project / "kernel.py").read_text() == "candidate\n"
    assert "Never revert" in workspace.prompt_block()
    assert (workspace.cache_root / "triton").is_dir()
    assert "never under profile/ or research/" in workspace.prompt_block()


def test_reuses_same_reference_for_same_head(tmp_path: Path) -> None:
    project = _repo(tmp_path / "project")
    first = prepare_baseline_workspace(project, tmp_path / "state")
    second = prepare_baseline_workspace(project, tmp_path / "state")

    assert first is not None and second is not None
    assert second.reference_root == first.reference_root


def test_refreshes_reference_after_source_head_changes(tmp_path: Path) -> None:
    project = _repo(tmp_path / "project")
    first = prepare_baseline_workspace(project, tmp_path / "state")
    assert first is not None
    (project / "kernel.py").write_text("new baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "kernel.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "next"], cwd=project, check=True)

    second = prepare_baseline_workspace(project, tmp_path / "state")

    assert second is not None
    assert (second.reference_root / "kernel.py").read_text() == "new baseline\n"


def test_non_git_project_has_no_reference_workspace(tmp_path: Path) -> None:
    project = tmp_path / "plain"
    project.mkdir()

    assert prepare_baseline_workspace(project, tmp_path / "state") is None
