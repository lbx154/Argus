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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from argus_skill.webapi.map_notes import append_note, list_notes, notes_path

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from argus_skill.webapi import server  # noqa: E402

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


def test_note_roundtrip(client: TestClient, tmp_path: Path) -> None:
    posted = client.post(
        f"/api/projects/{_SID}/map-notes",
        json={"node_id": "task-a", "text": "先别动这条线", "author": "web"},
    )
    assert posted.status_code == 200
    note = posted.json()["note"]
    assert note["node_id"] == "task-a"
    assert note["text"] == "先别动这条线"
    assert note["author"] == "web"
    assert note["id"]
    assert note["ts"] > 0

    fetched = client.get(f"/api/projects/{_SID}/map-notes")
    assert fetched.status_code == 200
    notes = fetched.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["id"] == note["id"]
    assert notes[0]["text"] == "先别动这条线"

    # Storage is per-session JSONL in the life dir.
    life = tmp_path / "projects" / _SID
    stored = [
        json.loads(line)
        for line in (life / "map_notes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["id"] for row in stored] == [note["id"]]


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


def test_missing_file_lists_nothing(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-notes-empty")
    assert not notes_path(life).exists()
    assert list_notes(life) == []


def test_malformed_rows_are_skipped(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-notes-bad")
    append_note(life, node_id="task-a", text="good")
    with notes_path(life).open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"text": "no node id"}) + "\n")
    notes = list_notes(life)
    assert [row["text"] for row in notes] == ["good"]


def test_concurrent_appends_all_land_as_valid_rows(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-notes-race")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda index: append_note(life, node_id="task-a", text=f"race {index}"),
            range(16),
        ))
    lines = notes_path(life).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert len(rows) == 16
    assert len({row["id"] for row in rows}) == 16
    assert {row["text"] for row in rows} == {f"race {i}" for i in range(16)}


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
