from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from argus.core.workspace_lease import (
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


@pytest.mark.parametrize("failure", [
    "serialization",
    pytest.param("sidecar", marks=pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")),
])
def test_failed_lease_initialization_releases_lock_for_same_process(tmp_path, failure) -> None:
    # A subprocess contains any leaked descriptor when this regression fails.
    # Both attempts below still happen within that one process, without restart.
    code = textwrap.dedent(f"""
        from pathlib import Path
        from argus.core import workspace_lease as lease

        root = Path({str(tmp_path)!r})
        workspace = root / 'workspace'
        workspace.mkdir()
        lease.tempfile.gettempdir = lambda: str(root / 'leases')
        if {failure!r} == 'serialization':
            try:
                lease.acquire_workspace_lease(workspace, owner={{'invalid': object()}})
            except TypeError:
                pass
            else:
                raise AssertionError('invalid owner must still report its serialization error')
        else:
            owner_path = lease.workspace_lease_path(workspace).with_suffix('.owner.json')
            owner_path.write_text('{{"sid":"previous-owner"}}', encoding='utf-8')
            with owner_path.open('rb'):
                try:
                    lease.acquire_workspace_lease(workspace, owner={{'sid':'first-attempt'}})
                except PermissionError:
                    pass
                else:
                    raise AssertionError('the real Windows read handle must prevent replacement')

        acquired = lease.acquire_workspace_lease(workspace, owner={{'sid':'second-attempt'}})
        lease.release_workspace_lease(acquired)
        print('released')
    """)
    result = subprocess.run(
        [sys.executable, '-c', code], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ['released']
