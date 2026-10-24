"""/api/projects/{sid}/canvas — human cards, human links and remembered places."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.webapi import canvas as store

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from argus.webapi import server  # noqa: E402

_SID = "s-canvas"
_BASE = f"/api/projects/{_SID}/canvas"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    life = tmp_path / "projects" / _SID
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(json.dumps({"type": "mission.started", "ts": time.time()}) + "\n", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return TestClient(server.create_app(global_root=tmp_path))


def _card(client: TestClient, revision: int, **body) -> dict:
    res = client.post(f"{_BASE}/cards", json={"revision": revision, **body})
    assert res.status_code == 200, res.text
    return res.json()


def test_empty_canvas(client: TestClient) -> None:
    res = client.get(_BASE)
    assert res.status_code == 200
    assert res.json() == store.empty_canvas()


def test_card_link_position_roundtrip(client: TestClient, tmp_path: Path) -> None:
    idea = _card(client, 0, kind="idea", text="先验证数据口径", position={"x": 10, "y": 20})
    assert idea["canvas"]["revision"] == 1
    card = idea["card"]
    assert card["id"].startswith("hc-") and card["text"] == "先验证数据口径"
    assert idea["canvas"]["positions"][card["id"]] == {"x": 10, "y": 20}

    filed = _card(client, 1, kind="file", attachment={
        "attachment_id": "att-1", "name": "spec.pdf", "relative_path": "attachments/att-1/spec.pdf",
        "mime": "application/pdf", "size_bytes": 12})
    file_id = filed["card"]["id"]
    assert filed["card"]["attachment"]["name"] == "spec.pdf"

    # Links may join a human card and an Argus task card.
    link = client.post(f"{_BASE}/links", json={"revision": 2, "source": file_id, "target": "task-a", "kind": "basis"})
    assert link.status_code == 200, link.text
    link_id = link.json()["link"]["id"]
    again = client.post(f"{_BASE}/links", json={"revision": 3, "source": card["id"], "target": file_id, "kind": "related"})
    assert again.status_code == 200

    moved = client.put(f"{_BASE}/positions", json={"revision": 4, "positions": {"task-a": {"x": 500, "y": -40}}})
    assert moved.status_code == 200
    assert moved.json()["canvas"]["positions"]["task-a"] == {"x": 500, "y": -40}

    patched = client.patch(f"{_BASE}/cards/{card['id']}", json={"revision": 5, "text": "改成：先看口径"})
    assert patched.json()["card"]["text"] == "改成：先看口径"
    relinked = client.patch(f"{_BASE}/links/{link_id}", json={"revision": 6, "kind": "check"})
    assert relinked.json()["link"]["kind"] == "check"

    # Deleting a human card drops its links and place.
    gone = client.delete(f"{_BASE}/cards/{card['id']}", params={"revision": 7})
    assert gone.status_code == 200
    canvas = client.get(_BASE).json()
    assert [c["id"] for c in canvas["cards"]] == [file_id]
    assert [lk["id"] for lk in canvas["links"]] == [link_id]
    assert card["id"] not in canvas["positions"]
    assert client.delete(f"{_BASE}/links/{link_id}", params={"revision": 8}).status_code == 200
    assert client.get(_BASE).json()["revision"] == 9
    assert (tmp_path / "projects" / _SID / "canvas.json").is_file()


def test_stale_revision_conflicts(client: TestClient) -> None:
    _card(client, 0, kind="idea", text="a")
    stale = client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "idea", "text": "b"})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["canvas"]["revision"] == 1
    assert [c["text"] for c in detail["canvas"]["cards"]] == ["a"]
    assert client.put(f"{_BASE}/positions", json={"revision": 7, "positions": {}}).status_code == 409


def test_task_cards_cannot_be_deleted_or_edited(client: TestClient) -> None:
    assert client.delete(f"{_BASE}/cards/task-a", params={"revision": 0}).status_code == 404
    assert client.patch(f"{_BASE}/cards/task-a", json={"revision": 0, "text": "x"}).status_code == 404
    assert client.get(_BASE).json()["revision"] == 0


def test_validation_and_caps(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    too_long = client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "idea", "text": "x" * (store.MAX_TEXT_CHARS + 1)})
    assert too_long.status_code == 422
    assert client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "idea", "text": "  "}).status_code == 422
    assert client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "file"}).status_code == 422
    assert client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "shape", "text": "x"}).status_code == 422
    assert client.post(f"{_BASE}/links", json={"revision": 0, "source": "a", "target": "a", "kind": "basis"}).status_code == 422
    assert client.post(f"{_BASE}/links", json={"revision": 0, "source": "a", "target": "b", "kind": "likes"}).status_code == 422
    assert client.post(f"{_BASE}/links", json={"revision": 0, "source": "hc-gone", "target": "b", "kind": "basis"}).status_code == 404
    far = client.put(f"{_BASE}/positions", json={"revision": 0, "positions": {"a": {"x": 1e12, "y": 0}}})
    assert far.status_code == 422

    monkeypatch.setattr(store, "MAX_CARDS", 2)
    _card(client, 0, kind="idea", text="1")
    _card(client, 1, kind="idea", text="2")
    full = client.post(f"{_BASE}/cards", json={"revision": 2, "kind": "idea", "text": "3"})
    assert full.status_code == 413
    monkeypatch.setattr(store, "MAX_LINKS", 1)
    assert client.post(f"{_BASE}/links", json={"revision": 2, "source": "a", "target": "b", "kind": "order"}).status_code == 200
    # The same link twice is the same link, not a second one.
    assert client.post(f"{_BASE}/links", json={"revision": 3, "source": "b", "target": "a", "kind": "order"}).status_code == 200
    assert client.post(f"{_BASE}/links", json={"revision": 4, "source": "a", "target": "c", "kind": "order"}).status_code == 413


def test_digest_lists_linked_cards_first(tmp_path: Path) -> None:
    canvas, first = store.add_card(tmp_path, 0, kind="idea", text="早的想法")
    canvas, second = store.add_card(tmp_path, 1, kind="idea", text="晚的想法")
    canvas, _ = store.add_link(tmp_path, 2, source=first["id"], target="task-x", kind="check")
    lines = store.human_canvas_digest(canvas, titles={"task-x": "跑基线"})
    assert lines[0].endswith("早的想法") and lines[1].endswith("晚的想法")
    assert lines[-1] == "- 连线（检查）: 想法卡《早的想法》 → 任务《跑基线》"


def _canvas_events(tmp_path: Path) -> list[dict]:
    rows = (tmp_path / "projects" / _SID / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [row for row in map(json.loads, rows) if row.get("type") == "project.canvas.updated"]


def test_every_accepted_write_is_announced_on_the_live_stream(client: TestClient, tmp_path: Path) -> None:
    """Other open windows refetch on the event instead of waiting for a poll."""
    card = _card(client, 0, kind="idea", text="先确认评测口径")["card"]
    res = client.put(f"{_BASE}/positions", json={"revision": 1, "positions": {card["id"]: {"x": 5, "y": 6}}})
    assert res.status_code == 200, res.text
    stale = client.post(f"{_BASE}/cards", json={"revision": 0, "kind": "idea", "text": "x"})
    assert stale.status_code == 409
    events = _canvas_events(tmp_path)
    assert [(e["revision"], e["change"]) for e in events] == [(1, "card"), (2, "positions")]
    assert all(e.get("event_validation") is None for e in events)
