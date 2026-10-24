"""GET/POST /api/projects/{sid}/map-notes — operator notes pinned to map nodes.

A note is one JSONL row ({id, node_id, text, author, ts}) in the session's
``map_notes.jsonl``, appended under the same sibling-lock convention the
backlog uses. GET returns the latest 200, oldest first. The rows are also the
source of the Planner digest section tested in
``tests/life/test_operator_map_notes_digest.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.webapi.map_notes import append_note, list_notes

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from argus.webapi import server  # noqa: E402

_SID = "s-notes"


def _make_project(root: Path, sid: str = _SID) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


def test_note_validation_bounds(client: TestClient) -> None:
    path = f"/api/projects/{_SID}/map-notes"
    assert client.post(path, json={"node_id": "", "text": "x"}).status_code == 422
    assert client.post(path, json={"node_id": "task-a", "text": ""}).status_code == 422
    assert (
        client.post(path, json={"node_id": "task-a", "text": "长" * 2001}).status_code
        == 422
    )
    assert (
        client.post(path, json={"node_id": "task-a", "text": "长" * 2000}).status_code
        == 200
    )


def test_get_returns_latest_200_oldest_first(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-notes-cap")
    for index in range(205):
        append_note(life, node_id="task-a", text=f"note {index}")
    notes = list_notes(life)
    assert len(notes) == 200
    assert notes[0]["text"] == "note 5"
    assert notes[-1]["text"] == "note 204"


def test_routes_require_auth_and_known_project(tmp_path: Path) -> None:
    _make_project(tmp_path)
    client = TestClient(server.create_app(global_root=tmp_path, auth_token="test"))
    path = f"/api/projects/{_SID}/map-notes"
    assert client.get(path).status_code == 401
    headers = {"Authorization": "Bearer test"}
    assert client.get(path, headers=headers).status_code == 200
    assert (
        client.get("/api/projects/s-nope/map-notes", headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            "/api/projects/s-nope/map-notes",
            json={"node_id": "task-a", "text": "x"},
            headers=headers,
        ).status_code
        == 404
    )
