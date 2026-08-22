"""A rename must never be hidden behind the listing cache.

``/api/projects`` is coalesced (see :mod:`tests.webapi.test_index_cache`) so a
cockpit with several tabs open does not melt the server. That cache is only
safe if a mutation drops it: an operator who renames a session and sees the old
name come back on the next poll reads it as "my change did not take", and the
harness has taught them to distrust the UI.

Invalidation is keyed off the HTTP method rather than a list of routes, so a
mutating endpoint added later cannot forget to do it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi import server

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    sid = "s-cachetest"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True, exist_ok=True)
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            created=1,
            last_active=1,
            cwd=str(life_dir),
            display_name="before rename",
        ),
    )
    return tmp_path


def _names(client: TestClient) -> list[str]:
    payload = client.get("/api/projects").json()
    return [str(project.get("label") or "") for project in payload["projects"]]


def _snapshot_name(client: TestClient) -> str:
    payload = client.get("/api/projects/s-cachetest/snapshot?compact=true&events_limit=30").json()
    return str(payload["session"].get("display_name") or "")


def test_a_rename_is_visible_on_the_very_next_poll(home: Path) -> None:
    client = TestClient(server.create_app(global_root=home))

    assert "before rename" in _names(client)
    assert _snapshot_name(client) == "before rename"

    response = client.patch("/api/projects/s-cachetest", json={"name": "after rename"})
    assert response.status_code == 200

    # No sleep: the point is that the operator does not have to wait out a TTL.
    assert "after rename" in _names(client)
    assert _snapshot_name(client) == "after rename"


def test_project_index_uses_durable_activity(home: Path) -> None:
    events = home / "projects" / "s-cachetest" / "events.jsonl"
    events.write_text('{"type":"round.start"}\n')
    os.utime(events, (100, 100))
    client = TestClient(server.create_app(global_root=home))

    payload = client.get("/api/projects").json()
    project = next(row for row in payload["projects"] if row["id"] == "s-cachetest")
    snapshot = client.get(
        "/api/projects/s-cachetest/snapshot?compact=true&events_limit=30"
    ).json()

    assert project["last_active"] == 100
    assert snapshot["session"]["last_active"] == 100


def test_repeated_polls_reuse_one_scan(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The saving that keeps the cockpit responsive, measured at the route."""
    app = server.create_app(global_root=home)
    client = TestClient(app)

    client.get("/api/projects")

    from argus_skill.webapi.routes.context import ServerContext

    scans: list[int] = []
    original = ServerContext._machine_projects_uncached

    def counting(self, **kwargs):  # noqa: ANN001, ANN003
        scans.append(1)
        return original(self, **kwargs)

    monkeypatch.setattr(ServerContext, "_machine_projects_uncached", counting)

    for _ in range(10):
        client.get("/api/projects")

    assert scans == []


def test_repeated_trash_polls_reuse_one_scan(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scans: list[int] = []
    original = server.list_trashed_projects

    def counting(*, global_root):  # noqa: ANN001
        scans.append(1)
        return original(global_root=global_root)

    monkeypatch.setattr(server, "list_trashed_projects", counting)
    client = TestClient(server.create_app(global_root=home))

    for _ in range(10):
        assert client.get("/api/trash").status_code == 200

    assert scans == [1]


def test_repeated_snapshot_polls_reuse_one_build(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds: list[int] = []
    original = server.build_snapshot

    def counting(*args, **kwargs):  # noqa: ANN002, ANN003
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "build_snapshot", counting)
    client = TestClient(server.create_app(global_root=home))

    for _ in range(10):
        response = client.get("/api/projects/s-cachetest/snapshot?compact=true&events_limit=30")
        assert response.status_code == 200

    assert builds == [1]


def test_repeated_compact_snapshot_polls_do_not_start_manager_contexts(
    home: Path,
) -> None:
    from argus_skill.webapi import manager_state

    manager_state._STATES.clear()
    client = TestClient(server.create_app(global_root=home))

    for _ in range(10):
        response = client.get("/api/projects/s-cachetest/snapshot?compact=true&events_limit=30")
        assert response.status_code == 200

    assert manager_state._STATES == {}


def test_active_snapshot_polls_schedule_one_manager_prewarm(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prewarms: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        "argus_skill.webapi.manager_state.schedule_manager_prewarm",
        lambda sid, *, global_root=None: prewarms.append((sid, Path(global_root))),
    )
    client = TestClient(server.create_app(global_root=home))

    response = client.get(
        "/api/projects/s-cachetest/snapshot"
        "?compact=true&events_limit=30&prewarm=true"
    )

    assert response.status_code == 200
    assert prewarms == [("s-cachetest", home)]


def test_a_delete_is_visible_on_the_very_next_poll(home: Path) -> None:
    client = TestClient(server.create_app(global_root=home))

    assert _names(client) != []
    assert client.get("/api/trash").json()["entries"] == []

    response = client.delete("/api/projects/s-cachetest")
    assert response.status_code == 200

    assert _names(client) == []
    assert len(client.get("/api/trash").json()["entries"]) == 1
