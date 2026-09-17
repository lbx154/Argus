from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus.core.campaign_workdir import (
    active_campaign_workdir,
    adopt_campaign_workdir,
    normalize_task_workdir,
    resolve_task_workdir,
)
from argus.skills.vertical_select import persist_vertical, resolve_vertical


def _git_init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _workspace_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def test_normalize_task_workdir_rejects_escape_and_absolute(tmp_path: Path) -> None:
    assert normalize_task_workdir(".") == ""
    assert normalize_task_workdir("repo/") == "repo"
    with pytest.raises(ValueError, match="project-relative"):
        normalize_task_workdir("../repo")
    with pytest.raises(ValueError, match="project-relative"):
        normalize_task_workdir(str(tmp_path / "repo"))


def test_normalize_task_workdir_accepts_absolute_path_inside_active_project(
    tmp_path: Path,
) -> None:
    child = tmp_path / "repo"
    child.mkdir()

    assert normalize_task_workdir(str(tmp_path), base_root=tmp_path) == ""
    assert normalize_task_workdir(str(child), base_root=tmp_path) == "repo"
    with pytest.raises(ValueError, match="inside the active project"):
        normalize_task_workdir(str(tmp_path.parent), base_root=tmp_path)


def test_resolve_task_workdir_accepts_directory(tmp_path: Path) -> None:
    child = tmp_path / "repo"
    child.mkdir()

    assert resolve_task_workdir(tmp_path, "repo") == child.resolve()
    with pytest.raises(ValueError, match="not a directory"):
        resolve_task_workdir(tmp_path, "missing")


def test_resolve_task_workdir_allows_symlink_outside_workspace(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (base / "target").symlink_to(external, target_is_directory=True)

    assert resolve_task_workdir(base, "target") == external.resolve()


@pytest.mark.parametrize("external", [False, True], ids=["nested", "symlinked"])
def test_adoption_preserves_execution_workspace(
    tmp_path: Path,
    external: bool,
    request: pytest.FixtureRequest,
) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    target = tmp_path / "external" if external else base / "target"
    _git_init(target)
    requested = base / "target"
    if external:
        request.getfixturevalue("require_symlink_support")
        requested.symlink_to(target, target_is_directory=True)
    (target / "src").mkdir()
    (target / "src" / "module.py").write_bytes(b"VALUE = 1\n")
    (target / "notes").mkdir()
    (target / "notes" / "research.txt").write_bytes(b"user-owned\x00content")
    state = tmp_path / "life"
    persist_vertical(state, "software")
    before = _workspace_snapshot(target)

    adopted = adopt_campaign_workdir(
        state_root=state,
        base_root=base,
        current_root=base,
        requested="target",
    )

    assert adopted == target.resolve()
    assert active_campaign_workdir(state, base) == target.resolve()
    assert _workspace_snapshot(target) == before
    assert resolve_vertical(state) == "software"


def test_adoption_is_ignored_after_session_base_workdir_changes(
    tmp_path: Path,
) -> None:
    original_base = tmp_path / "workspace"
    original_base.mkdir()
    old_target = original_base / "old-target"
    _git_init(old_target)
    state = tmp_path / "life"
    adopt_campaign_workdir(
        state_root=state,
        base_root=original_base,
        current_root=original_base,
        requested="old-target",
    )
    new_base = tmp_path / "new-target"
    _git_init(new_base)

    assert active_campaign_workdir(state, original_base) == old_target.resolve()
    assert active_campaign_workdir(state, new_base) is None


def test_repeated_preplanned_child_path_is_idempotent_after_adoption(
    tmp_path: Path,
) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    child = base / "target"
    _git_init(child)
    state = tmp_path / "life"

    first = adopt_campaign_workdir(
        state_root=state,
        base_root=base,
        current_root=base,
        requested="target",
    )
    second = adopt_campaign_workdir(
        state_root=state,
        base_root=base,
        current_root=first,
        requested="target",
    )

    assert second == child.resolve()


def test_adoption_requires_git_toplevel(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    plain = base / "plain"
    plain.mkdir(parents=True)

    with pytest.raises(ValueError, match="real Git repository"):
        adopt_campaign_workdir(
            state_root=tmp_path / "life",
            base_root=base,
            current_root=base,
            requested="plain",
        )


def test_web_session_exposes_the_effective_campaign_root(tmp_path: Path) -> None:
    from argus.webapi.project_state import apply_campaign_workdir

    base = tmp_path / "workspace"
    base.mkdir()
    child = base / "target"
    _git_init(child)
    state = tmp_path / "life"
    adopt_campaign_workdir(
        state_root=state,
        base_root=base,
        current_root=base,
        requested="target",
    )

    session = apply_campaign_workdir(
        {"id": "s-one", "workdir": str(base), "cwd": str(state)}, state
    )

    assert session["session_workdir"] == str(base)
    assert session["campaign_workdir"] == str(child.resolve())
    assert session["workdir"] == str(child.resolve())


def test_invalid_persisted_root_is_ignored(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    state = tmp_path / "life"
    state.mkdir()
    (state / "campaign-workdir.json").write_text(
        json.dumps({"workdir": str(tmp_path / "missing")}),
        encoding="utf-8",
    )

    assert active_campaign_workdir(state, base) is None
