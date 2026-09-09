"""Concurrency and single-traversal guarantees for the live map feed."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.team import task_board
from argus_skill.webapi import map_feed, map_team
from argus_skill.webapi.map_feed import MapFeed
from argus_skill.webapi.server import create_app


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


def team_session(root: Path, sid: str = "s-team-perf"):
    workdir = root / "workspaces" / sid
    workdir.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, workdir=str(workdir), created=1))
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(
        id="parent", ts=1, title="Choose a research idea", objective="Compare routes", status="running",
    ))
    board = workdir / ".argus" / "teams" / "research-team"
    task_board.form(board, [
        {"task_id": "route-01", "role": "idea-route", "title": "Investigate route one",
         "objective": "Read primary sources for route one."},
    ])
    append(life, {"type": "life.mission.started", "item_id": "parent", "ts": 2})
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent", "team_root": str(board), "ts": 10})
    return sid, life, board


def team_events(value: dict) -> dict[str, dict]:
    return {event["team_task_id"]: event for event in value["events"] if event["type"] == "team.task"}


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


def test_invalidating_read_walks_team_sources_once(tmp_path, monkeypatch):
    sid, life, board = team_session(tmp_path)
    feed = MapFeed()
    feed.read(sid, tmp_path, life)
    task_board.claim_top(board, "worker-1", now=20)
    calls = []
    original = map_team._sources

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(map_team, "_sources", counted)
    changed = feed.read(sid, tmp_path, life)
    assert team_events(changed)["route-01"]["status"] == "claimed"
    assert len(calls) == 1


def test_formations_recorded_since_the_signature_are_projected_immediately(tmp_path):
    sid, life, board = team_session(tmp_path)
    feed = MapFeed()
    feed.read(sid, tmp_path, life)
    second = board.with_name("second-team")
    task_board.form(second, [
        {"task_id": "route-02", "role": "idea-route", "title": "Investigate route two",
         "objective": "Investigate a distinct route."},
    ])
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent", "team_root": str(second), "ts": 20})
    events = team_events(feed.read(sid, tmp_path, life))
    assert {"route-01", "route-02"} <= set(events)
    assert events["route-02"]["ts"] == 20


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
