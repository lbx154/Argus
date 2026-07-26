"""A running daemon must be visible in the cockpit that can stop it.

The Web sidebar deliberately hides legacy cwd-fingerprint project dirs: without
a session.json they have no stable label contract and would show up as
mysterious hex rows. That is a reasonable default for inert litter.

It is the wrong answer for a *live* daemon. Testing on 2026-07-26 I started five
daemons with `argus --daemon-fg`, opened the cockpit, and saw an empty project
list — the running work could be neither observed nor stopped from the UI that
exists to do both. The row is also not mysterious: the label the picker computes
for these is already the campaign objective.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from argus_skill.webapi.routes.context import ServerContext


class _Ctx(ServerContext):
    """The real filter, fed a controlled project list."""

    def __init__(self, root: Path, projects: list[dict[str, Any]]) -> None:
        self.roots = [root]
        self._projects = projects

    def _list_projects(self, *, global_root, limit, include_empty):  # noqa: ANN001
        _ = (global_root, limit, include_empty)
        return list(self._projects)


def _project(sid: str, *, alive: bool) -> dict[str, Any]:
    return {
        "id": sid,
        "label": "Benchmark this machine GPU",
        "daemon_alive": alive,
        "last_active": 1.0,
    }


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_a_running_daemon_without_a_session_is_still_listed(root: Path) -> None:
    ctx = _Ctx(root, [_project("e8d2340c8962", alive=True)])

    listed = ctx.machine_projects(limit=50, include_empty=False)

    assert [p["id"] for p in listed] == ["e8d2340c8962"]


def test_inert_hex_litter_is_still_hidden(root: Path) -> None:
    """The reason the filter exists in the first place."""
    ctx = _Ctx(root, [_project("e8d2340c8962", alive=False)])

    assert ctx.machine_projects(limit=50, include_empty=False) == []


def test_a_real_session_is_listed_either_way(root: Path) -> None:
    ctx = _Ctx(root, [_project("s-140a0353", alive=False)])

    listed = ctx.machine_projects(limit=50, include_empty=False)

    assert [p["id"] for p in listed] == ["s-140a0353"]
