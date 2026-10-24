import json
from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import map_history
from argus.webapi.map_feed import MapFeed
from argus.webapi.map_view import read_map
from argus.webapi.server import create_app


def setup_session(root, sid="s-progress"):
    write_session_meta(root, SessionMeta(id=sid, created=1, last_active=1))
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(id="a", ts=1, title="Coverage", objective="Compare methods", status="running"))
    append(life, {"type": "life.mission.started", "item_id": "a", "ts": 2})
    return sid, life, memory


def append(life, value):
    with (life / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def test_append_keeps_active_window_and_waits_for_complete_json_line(tmp_path):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    with (life / "events.jsonl").open("a") as stream:
        stream.write('{"type":"round.start","ts":3')
    partial = feed.read(sid, tmp_path, life, first["cursor"])
    assert not partial["events"]
    with (life / "events.jsonl").open("a") as stream:
        stream.write('}\n')
    complete = feed.read(sid, tmp_path, life, first["cursor"])
    assert len(complete["events"]) == 1 and complete["events"][0]["item_id"] == "a"
    assert feed.read(sid, tmp_path, life, complete["cursor"])["events"] == []


def test_history_size_check_never_reads_events_or_generates_copy(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    with (life / "events.jsonl").open("ab") as stream:
        stream.truncate(9 * 1024 * 1024)
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path != life / "events.jsonl", "Size check opened event contents"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with TestClient(create_app(global_root=tmp_path, auth_token="test")) as client:
        url = f"/api/projects/{sid}/map-info"
        assert client.get(url).status_code == 401
        info = client.get(url, headers={"Authorization": "Bearer test"}).json()
    assert info["requires_choice"] and info["task_count"] == 1
    assert info["current_task_id"] == "a"
    assert not (tmp_path / "map-presentation").exists()
    assert not (tmp_path / "map-history-cache").exists()


def test_current_range_excludes_earlier_tasks_with_the_same_timestamp(tmp_path):
    sid, life, memory = setup_session(tmp_path)
    memory.backlog.add(BacklogItem(id="b", ts=1, title="Current", objective="Validate", deps=["a"]))
    append(life, {"type": "life.mission.started", "item_id": "b", "ts": 4})
    with TestClient(create_app(global_root=tmp_path)) as client:
        data = client.get(f"/api/projects/{sid}/map?since=1&event_since=4&start_task=b").json()
    assert [t["id"] for t in data["tasks"]] == ["b"]
    assert data["tasks"][0]["deps"] == ["a"]
    assert [e["item_id"] for e in data["events"]] == ["b"]


def test_history_reset_returns_tasks_even_with_an_unchanged_task_cursor(tmp_path):
    sid, life, _ = setup_session(tmp_path)
    with TestClient(create_app(global_root=tmp_path)) as client:
        first = client.get(f"/api/projects/{sid}/map-history").json()
        (life / "events.jsonl").write_text("")
        reset = client.get(f"/api/projects/{sid}/map-history", params={
            "after": first["history_cursor"], "task_after": first["cursor"],
        }).json()
    assert reset["reset_history"]
    assert reset["tasks_complete"]
    assert [task["id"] for task in reset["tasks"]] == ["a"]


def test_history_includes_retained_generations_and_continues_after_rollover(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    events = life / "events.jsonl"
    events.rename(life / "events.jsonl.2")
    append(life, {"event_id": "middle", "type": "round.start", "ts": 3})
    events.rename(life / "events.jsonl.1")
    append(life, {"event_id": "latest", "type": "round.review.completed", "ts": 4})
    value = read_map(sid, tmp_path, life, include_events=False)
    monkeypatch.setattr(map_history, "PAGE_BYTES", 40)
    seen, cursor = [], None
    for _ in range(10):
        page = map_history.history_page(tmp_path, life, value, cursor)
        seen.extend(page["events"])
        cursor = page["history_cursor"]
        if not page["history_loading"]:
            break
    assert [event["type"] for event in seen] == [
        "life.mission.started", "round.start", "round.review.completed",
    ]
    assert all(event["item_id"] == "a" for event in seen)
    assert page["history_progress"]["loaded_bytes"] == sum(
        path.stat().st_size for path in life.glob("events.jsonl*")
    )
    assert map_history.history_info(value, life)["event_bytes"] == page["history_progress"]["loaded_bytes"]

    (life / "events.jsonl.1").rename(life / "events.jsonl.3")
    events.rename(life / "events.jsonl.1")
    append(life, {"event_id": "after-roll", "type": "round.start", "ts": 5})
    continued = map_history.history_page(tmp_path, life, value, cursor)
    assert not continued["reset_history"]
    assert [event["id"] for event in continued["events"]] == ["after-roll"]
    assert continued["events"][0]["item_id"] == "a"


def test_feed_preserves_client_history_after_restart_or_cursor_expiry(tmp_path):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    append(life, {"event_id": "during-restart", "item_id": "a", "type": "round.start", "ts": 3})
    restarted = MapFeed().read(sid, tmp_path, life, first["cursor"])
    assert not restarted["incremental"]
    assert not restarted["reset_history"]
    for number in range(10):
        append(life, {"event_id": f"round-{number}", "item_id": "a", "type": "round.start", "ts": number + 3})
        feed.read(sid, tmp_path, life)
    expired = feed.read(sid, tmp_path, life, first["cursor"])
    assert not expired["incremental"]
    assert not expired["reset_history"]


def test_feed_preserves_loaded_evidence_when_the_live_log_rolls_over(tmp_path):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    (life / "events.jsonl").rename(life / "events.jsonl.1")
    append(life, {"event_id": "after-roll", "item_id": "a", "type": "round.start", "ts": 3})
    continued = feed.read(sid, tmp_path, life, first["cursor"])
    assert continued["incremental"]
    assert not continued.get("reset_history")
    assert [event["id"] for event in continued["events"]] == ["after-roll"]
