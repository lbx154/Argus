from __future__ import annotations

import os

import pytest

from argus_skill.core.workspace_lease import (
    WorkspaceLeaseBusy,
    acquire_workspace_lease,
    release_workspace_lease,
    workspace_lease_path,
)


def test_workspace_lease_path_exposes_canonical_workdir(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "nested" / "workspace"
    workspace.mkdir(parents=True)
    temp_root = tmp_path / "leases"
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp_root))

    path = workspace_lease_path(workspace)

    uid = os.getuid() if hasattr(os, "getuid") else 0
    expected_root = temp_root / f"argus-skill-workspaces-{uid}"
    assert path == expected_root.joinpath(*workspace.resolve().parts[1:], "lease.lock")
    assert path.parent.is_dir()


def test_workspace_lease_is_exclusive_for_canonical_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = acquire_workspace_lease(workspace, owner={"sid": "s-one"})
    try:
        with pytest.raises(WorkspaceLeaseBusy, match="s-one"):
            acquire_workspace_lease(workspace / ".", owner={"sid": "s-two"})
    finally:
        release_workspace_lease(first)

    second = acquire_workspace_lease(workspace, owner={"sid": "s-two"})
    release_workspace_lease(second)
