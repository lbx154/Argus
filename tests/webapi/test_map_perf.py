"""Concurrency and single-traversal guarantees for the live map feed."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import map_feed
from argus.webapi.map_feed import MapFeed
from argus.webapi.server import create_app


def append(life: Path, event: dict) -> None:
    with (life / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")


def setup_session(root, sid="s-progress"):
    write_session_meta(root, SessionMeta(id=sid, created=1, last_active=1))
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(id="a", ts=1, title="Coverage", objective="Compare methods", status="running"))
    append(life, {"type": "life.mission.started", "item_id": "a", "ts": 2})
    return sid, life, memory


def test_slow_session_read_does_not_block_other_sessions(tmp_path, monkeypatch):
    slow_sid, slow_life, _ = setup_session(tmp_path, "s-slow")
    fast_sid, fast_life, _ = setup_session(tmp_path, "s-fast")
    feed = MapFeed()
    entered, release = threading.Event(), threading.Event()
    original = map_feed.read_map

    def stalled(sid, *args, **kwargs):
        if sid == slow_sid:
            entered.set()
            assert release.wait(timeout=10), "slow read was never released"
        return original(sid, *args, **kwargs)

    monkeypatch.setattr(map_feed, "read_map", stalled)
    with ThreadPoolExecutor(max_workers=2) as executor:
        slow = executor.submit(feed.read, slow_sid, tmp_path, slow_life)
        assert entered.wait(timeout=5)
        try:
            fast = executor.submit(feed.read, fast_sid, tmp_path, fast_life).result(timeout=2)
        finally:
            release.set()
        assert [task["id"] for task in fast["tasks"]] == ["a"]
        assert [task["id"] for task in slow.result(timeout=5)["tasks"]] == ["a"]


def test_racing_reads_of_one_session_never_overlap_a_projection(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    active = threading.Semaphore(1)
    original = map_feed.read_map

    def guarded(*args, **kwargs):
        assert active.acquire(blocking=False), "concurrent read_map for one session"
        try:
            time.sleep(0.02)
            return original(*args, **kwargs)
        finally:
            active.release()

    monkeypatch.setattr(map_feed, "read_map", guarded)

    def read(index):
        append(life, {"type": "round.start", "item_id": "a", "ts": 10 + index})
        return feed.read(sid, tmp_path, life)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(read, range(4)))
    assert all([task["id"] for task in value["tasks"]] == ["a"] for value in results)


def test_current_range_request_uses_a_single_feed_read(tmp_path, monkeypatch):
    sid, life, memory = setup_session(tmp_path)
    memory.backlog.add(BacklogItem(id="b", ts=1, title="Current", objective="Validate", deps=["a"]))
    memory.backlog.add(BacklogItem(id="c", ts=5, title="Later", objective="Extend"))
    append(life, {"type": "life.mission.started", "item_id": "b", "ts": 4})
    calls = []
    original = MapFeed.read

    def counted(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MapFeed, "read", counted)
    with TestClient(create_app(global_root=tmp_path)) as client:
        data = client.get(f"/api/projects/{sid}/map?since=1&event_since=4&start_task=b").json()
        assert [task["id"] for task in data["tasks"]] == ["b", "c"]
        assert data["tasks"][0]["deps"] == ["a"]
        assert [event["item_id"] for event in data["events"]] == ["b"]
        assert "task_index" not in data
        assert len(calls) == 1
        fallback = client.get(f"/api/projects/{sid}/map?since=5").json()
        assert [task["id"] for task in fallback["tasks"]] == ["c"]
        assert "task_index" not in fallback
        assert len(calls) == 2
