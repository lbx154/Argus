from __future__ import annotations

import pytest

from argus_skill.core.workspace_lease import (
    WorkspaceLeaseBusy,
    acquire_workspace_lease,
    release_workspace_lease,
)


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
