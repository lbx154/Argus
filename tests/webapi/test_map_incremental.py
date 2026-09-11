import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi import map_history, map_narrative
from argus_skill.webapi.map_feed import MapFeed
from argus_skill.webapi.map_view import read_map
from argus_skill.webapi.server import create_app


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


def test_feed_reuses_unchanged_projection_and_returns_only_new_records(tmp_path, monkeypatch):
    sid, life, memory = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    with monkeypatch.context() as patch:
        patch.setattr("argus_skill.webapi.map_feed.read_map", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reread")))
        unchanged = feed.read(sid, tmp_path, life, first["cursor"])
    assert unchanged["tasks"] == unchanged["events"] == []
    append(life, {"type": "round.review.completed", "ts": 3, "summary": "Check coverage"})
    memory.backlog.add(BacklogItem(id="b", ts=4, title="Validation", objective="Validate", deps=["a"]))
    second = feed.read(sid, tmp_path, life, first["cursor"])
    assert [t["id"] for t in second["tasks"]] == ["b"]
    assert len(second["events"]) == 1
    assert second["events"][0]["item_id"] == "a"
    assert second["events"][0]["association"] == "single_active_window"
    assert second["tasks"][0]["deps"] == ["a"]


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


def test_completion_scope_survives_incremental_feed_and_old_history_cache(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    event = {"event_id": "ended", "type": "life.mission.completed", "item_id": "a",
             "ts": 3, "success": True, "overall_complete": False, "campaign_continues": True}
    append(life, event)
    delta = feed.read(sid, tmp_path, life, first["cursor"])
    assert not delta["tasks"]
    assert delta["events"][0]["overall_complete"] is False
    assert delta["events"][0]["campaign_continues"] is True
    snapshot = read_map(sid, tmp_path, life, include_events=False)
    with monkeypatch.context() as old:
        old.setattr(map_history, "HISTORY_VERSION", map_history.HISTORY_VERSION - 1)
        page = map_history.history_page(tmp_path, life, snapshot, None)
    monkeypatch.setattr(map_history, "PAGE_EVENTS", 1)
    rebuilt = map_history.history_page(tmp_path, life, snapshot, page["history_cursor"])
    assert rebuilt["reset_history"]
    completed = map_history.history_page(tmp_path, life, snapshot, rebuilt["history_cursor"])
    assert completed["events"][0]["id"] == "ended"
    assert completed["events"][0]["overall_complete"] is False
    assert completed["events"][0]["campaign_continues"] is True
    assert not completed["history_loading"]


def test_unknown_cursor_and_replaced_log_return_a_full_projection(tmp_path):
    sid, life, _ = setup_session(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    assert not feed.read(sid, tmp_path, life, "unknown")["incremental"]
    (life / "events.jsonl").write_text("")
    reset = feed.read(sid, tmp_path, life, first["cursor"])
    assert not reset["incremental"] and not reset["events"]
    assert reset["reset_history"]
    other, other_life, _ = setup_session(tmp_path, "s-other")
    assert not feed.read(other, tmp_path, other_life, first["cursor"])["incremental"]


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


def test_complete_history_pages_include_evidence_before_the_live_tail(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    append(life, {"event_id": "early", "type": "round.review.completed", "ts": 3, "text": "Early finding"})
    with (life / "events.jsonl").open("a") as stream:
        noise = json.dumps({"type": "tool.output", "text": "x" * 1024}) + "\n"
        stream.write(noise * 8500)
    append(life, {"event_id": "late", "type": "round.review.completed", "ts": 4, "text": "Later finding"})
    seen, after = [], None
    with TestClient(create_app(global_root=tmp_path)) as client:
        for _ in range(30):
            response = client.get(f"/api/projects/{sid}/map-history", params={"after": after} if after else {})
            assert response.status_code == 200, response.text
            page = response.json()
            seen.extend(page["events"])
            after = page["history_cursor"]
            if not page["history_loading"]:
                break
        else:
            raise AssertionError("History paging did not finish")
        assert [e["id"] for e in seen if e["id"] in {"early", "late"}] == ["early", "late"]
        assert all(e["item_id"] == "a" for e in seen)
        assert not (tmp_path / "map-presentation").exists()
        assert len({e["id"] for e in seen}) == len(seen)
        empty = client.get(f"/api/projects/{sid}/map-history", params={"after": after, "task_after": page["cursor"]}).json()
        assert empty["events"] == empty["tasks"] == []
        assert not empty["history_loading"]

        def generate(documents, *args, **kwargs):
            assert documents[0]["events"][0]["text"] == "Early finding"
            return {"cards": [{"key": "early", "title": "Review", "summary": "Early finding", "detail": "Evidence checked"}], "relations": []}

        monkeypatch.setattr(map_narrative, "configured", lambda: True)
        monkeypatch.setattr(map_narrative, "generate", generate)
        result = client.post(f"/api/map-copy/project/{sid}", json={
            "cards": [{"key": "early", "task_id": "a", "kind": "review", "event_ids": ["early"]}],
        })
        assert result.status_code == 200, result.text


def test_history_index_survives_reopening_and_resets_replaced_files(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    value = read_map(sid, tmp_path, life, include_events=False)
    monkeypatch.setattr(map_history, "PAGE_EVENTS", 1)
    append(life, {"event_id": "review", "type": "round.review.completed", "ts": 3})
    first = map_history.history_page(tmp_path, life, value, None)
    second = map_history.history_page(tmp_path, life, value, first["history_cursor"])
    assert first["history_loading"] and not second["history_loading"]
    assert second["events"][0]["id"] == "review"
    assert map_history.indexed_evidence(tmp_path, life, ["review"])[0]["item_id"] == "a"
    (life / "events.jsonl").write_text("")
    reset = map_history.history_page(tmp_path, life, value, second["history_cursor"])
    assert reset["reset_history"] and not reset["events"]


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


def test_history_rebuilds_events_missing_from_an_earlier_task_snapshot(tmp_path):
    sid, life, memory = setup_session(tmp_path)
    value = read_map(sid, tmp_path, life, include_events=False)
    memory.backlog.add(BacklogItem(id="b", ts=3, title="New work", objective="Validate"))
    append(life, {"event_id": "new-task", "item_id": "b", "type": "life.mission.started", "ts": 4})
    first = map_history.history_page(tmp_path, life, value, None)
    assert "new-task" not in [event["id"] for event in first["events"]]
    refreshed = read_map(sid, tmp_path, life, include_events=False)
    recovered = map_history.history_page(tmp_path, life, refreshed, first["history_cursor"])
    assert recovered["reset_history"]
    assert "new-task" in [event["id"] for event in recovered["events"]]


def test_history_keeps_loaded_pages_when_planning_appends_a_new_task(tmp_path):
    sid, life, memory = setup_session(tmp_path)
    first = map_history.history_page(tmp_path, life, read_map(sid, tmp_path, life), None)
    memory.backlog.add(BacklogItem(id="b", ts=3, title="New work", objective="Validate"))
    append(life, {"event_id": "new-task", "item_id": "b", "type": "life.mission.started", "ts": 4})
    continued = map_history.history_page(
        tmp_path, life, read_map(sid, tmp_path, life), first["history_cursor"],
    )
    assert not continued["reset_history"]
    assert [event["id"] for event in continued["events"]] == ["new-task"]


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


def test_completed_child_copy_is_stable_across_later_progress_and_model_changes(tmp_path, monkeypatch):
    sid, life, memory = setup_session(tmp_path)
    append(life, {"event_id": "review", "type": "round.review.completed", "ts": 3, "text": "First review"})
    calls = []

    def generate(documents, *args, **kwargs):
        calls.append(documents)
        return {"cards": [{"key": d["key"], "title": "Review", "summary": "First review", "detail": "Checked"} for d in documents], "relations": []}

    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "generate", generate)
    request = [{"key": "review", "task_id": "a", "kind": "review", "event_ids": ["review"]}]
    first = map_narrative.enrich(tmp_path, read_map(sid, tmp_path, life), request, "en-US", project_root=life)
    memory.backlog.update("a", status="done", notes="Later successful result", finished_ts=99)
    monkeypatch.setenv("ARGUS_SKILL_MAP_MODEL", "different-model")
    second = map_narrative.enrich(tmp_path, read_map(sid, tmp_path, life), request, "en-US", project_root=life)
    assert second["cached"] and len(calls) == 1
    assert second["cards"]["review"] == first["cards"]["review"]
    assert "status" not in calls[0][0]["task"]


@pytest.mark.parametrize("version", [None, 5, 6])
def test_legacy_copy_migrates_without_model_requests(tmp_path, monkeypatch, version):
    sid, life, _ = setup_session(tmp_path)
    value = read_map(sid, tmp_path, life)
    saved = {"version": version, "model_revision": "earlier-model",
             "task_revision": value["tasks"][0]["revision"], "event_ids": [],
             "title": "Coverage", "summary": "Existing summary", "detail": "Existing detail", "generated_at": 1}
    path = map_narrative.cache_path(tmp_path, value["id"] + ":en-US")
    path.parent.mkdir()
    path.write_text(json.dumps({"cards": {"a": saved}, "relations": []}))
    monkeypatch.setattr(map_narrative, "generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("regenerated")))
    result = map_narrative.enrich(tmp_path, value, [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}], "en-US", project_root=life)
    assert result["cached"] and result["cards"]["a"]["generated_at"] == 1
    assert result["cards"]["a"]["input_revision"]


def test_different_sessions_can_prepare_copy_concurrently(tmp_path, monkeypatch):
    sessions = [setup_session(tmp_path, sid) for sid in ("s-one", "s-two")]
    barrier = threading.Barrier(2)

    def generate(documents, *args, **kwargs):
        barrier.wait(timeout=5)
        return {"cards": [{"key": "a", "title": "Coverage", "summary": "Checked", "detail": "Independent result"}], "relations": []}

    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "generate", generate)

    def run(session):
        sid, life, _ = session
        return map_narrative.enrich(tmp_path, read_map(sid, tmp_path, life),
                                    [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}], "en-US", project_root=life)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, sessions))
    assert all(r["cache_revision"] == 1 for r in results)


def test_same_session_requests_share_the_existing_generation(tmp_path, monkeypatch):
    sid, life, _ = setup_session(tmp_path)
    data = read_map(sid, tmp_path, life)
    calls = []

    def generate(documents, *args, **kwargs):
        calls.append(documents)
        return {"cards": [{"key": "a", "title": "Coverage", "summary": "Checked", "detail": "Result"}], "relations": []}

    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "generate", generate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(map_narrative.enrich, tmp_path, data,
                                   [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}], "en-US", project_root=life) for _ in range(2)]
        results = [f.result() for f in futures]
    assert len(calls) == 1
    assert sum(r["cached"] for r in results) == 1


def test_separate_api_processes_reuse_the_same_cached_generation(tmp_path):
    script = """
import json, sys, time
from pathlib import Path
from argus_skill.webapi import map_narrative as copy
root = Path(sys.argv[1])
def generate(documents, *args, **kwargs):
    with (root / 'calls.txt').open('a') as f: f.write('call\\n')
    time.sleep(.15)
    return {'cards': [{'key': 'a', 'title': 'Coverage', 'summary': 'Checked', 'detail': 'Result'}], 'relations': []}
copy.generate = generate
copy.configured = lambda: True
data = {'id': 'live:s-process', 'tasks': [{'id': 'a', 'title': 'Coverage', 'status': 'done'}], 'events': []}
result = copy.enrich(root, data, [{'key': 'a', 'task_id': 'a', 'kind': 'task', 'event_ids': []}], 'en-US', project_root=root)
print(json.dumps({'cached': result['cached']}))
"""
    workers = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    receipts = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=30)
        assert worker.returncode == 0, stderr
        receipts.append(json.loads(stdout))
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["call"]
    assert sum(r["cached"] for r in receipts) == 1


def test_updated_event_behind_the_cursor_is_redelivered(tmp_path):
    sid, life, memory = setup_session(tmp_path)
    append(life, {"type": "round.review.completed", "event_id": "ev-x", "item_id": "a",
                  "ts": 3, "summary": "Round one passed", "status": "done"})
    value = read_map(sid, tmp_path, life, include_events=False)
    first = map_history.history_page(tmp_path, life, value, None)
    assert any(e["id"] == "ev-x" for e in first["events"])
    drained = map_history.history_page(tmp_path, life, value, first["history_cursor"])
    assert drained["events"] == []
    # The journal later rewrites the same event id — a step retired as
    # superseded. A reader already paged past its seq must still get the
    # update; an in-place upsert would hide it behind the cursor forever.
    append(life, {"type": "round.review.completed", "event_id": "ev-x", "item_id": "a",
                  "ts": 3, "summary": "Superseded by the revised plan", "status": "superseded"})
    update = map_history.history_page(tmp_path, life, value, drained["history_cursor"])
    bodies = {e["id"]: e for e in update["events"]}
    assert "ev-x" in bodies
    assert bodies["ev-x"]["status"] == "superseded"
