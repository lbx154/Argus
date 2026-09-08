from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.team import _store, task_board
from argus_skill.webapi import map_team
from argus_skill.webapi.map_feed import MapFeed
from argus_skill.webapi.map_view import read_map


def append(life: Path, event: dict) -> None:
    with (life / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")


def sample(root: Path, sid: str = "s-team-map"):
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
        {"task_id": "route-01-review", "role": "idea-review", "title": "Independently review route one",
         "objective": "Check the route's evidence.", "deps": ["route-01"]},
        {"task_id": "route-02", "role": "idea-route", "title": "Investigate route two",
         "objective": "Investigate a distinct route."},
    ])
    append(life, {"type": "life.mission.started", "item_id": "parent", "ts": 2})
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent", "team_root": str(board), "ts": 10})
    return sid, life, board


def team_events(value: dict) -> dict[str, dict]:
    return {event["team_task_id"]: event for event in value["events"] if event["type"] == "team.task"}


def test_team_tasks_remain_inside_the_recorded_macro_with_real_evidence(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    task_board.claim_top(board, "worker-1", now=20)
    task_board.heartbeat(board, "route-01", now=25)
    task_board.fail(board, "route-02", reason="Delegate exited before saving its report.")
    before = {path: path.read_bytes() for path in board.rglob("*") if path.is_file()}
    backlog_before = (life / "backlog.jsonl").read_bytes()
    value = read_map(sid, tmp_path, life)
    events = team_events(value)
    assert [task["id"] for task in value["tasks"]] == ["parent"]
    assert list(events) == ["route-01", "route-01-review", "route-02"]
    assert events["route-01"]["status"] == "running"
    assert events["route-01"]["owner"] == "worker-1"
    assert events["route-01"]["ts"] == 10
    assert events["route-01"]["started_ts"] == 20
    assert events["route-01"]["updated_ts"] == 20
    assert events["route-01-review"]["deps"] == [events["route-01"]["id"]]
    assert events["route-01-review"]["role"] == "reviewer"
    assert events["route-02"]["status"] == "failed"
    assert events["route-02"]["reason"] in events["route-02"]["text"]
    assert all(event["item_id"] == "parent" and event["association"] == "explicit" for event in events.values())
    assert not board.with_name(board.name + "-selection").exists()
    assert {path: path.read_bytes() for path in board.rglob("*") if path.is_file()} == before
    assert (life / "backlog.jsonl").read_bytes() == backlog_before


def test_team_only_progress_updates_the_feed_even_after_the_main_daemon_stops(tmp_path: Path, monkeypatch) -> None:
    sid, life, board = sample(tmp_path)
    (life / "daemon.status.json").write_text(json.dumps({"alive": False, "health": "stopped"}))
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    with monkeypatch.context() as patch:
        patch.setattr("argus_skill.webapi.map_feed.read_map", lambda *a, **k: pytest.fail("unchanged projection reread"))
        assert feed.read(sid, tmp_path, life, first["cursor"])["events"] == []
    main_before = (life / "events.jsonl").read_bytes()
    task_board.claim_top(board, "worker-1", now=30)
    changed = feed.read(sid, tmp_path, life, first["cursor"])
    assert changed["incremental"] and changed["tasks"] == []
    assert len(changed["events"]) == 1
    assert changed["events"][0]["status"] == "claimed"
    assert changed["events"][0]["ts"] == 10
    assert changed["cursor"] != first["cursor"]
    task_board.fail(board, "route-01", reason="Worker timed out.")
    failed = feed.read(sid, tmp_path, life, changed["cursor"])
    assert failed["events"][0]["reason"] == "Worker timed out."
    task_board.retry_terminal(board, "route-01")
    retry = feed.read(sid, tmp_path, life, failed["cursor"])
    assert retry["events"][0]["id"] == changed["events"][0]["id"]
    assert retry["events"][0]["status"] == "pending"
    assert retry["events"][0]["attempt"] == 1
    assert retry["events"][0]["ts"] == 10
    assert (life / "events.jsonl").read_bytes() == main_before


def test_atomic_replacement_and_deletion_invalidate_team_observations(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    path = board / "tasks" / "route-01.json"
    old = path.stat()
    directory = path.parent.stat()
    value = json.loads(path.read_text())
    value["state"] = "running"  # Same byte length as pending.
    _store.atomic_write_json(path, value)
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.utime(path.parent, ns=(directory.st_atime_ns, directory.st_mtime_ns))
    assert path.stat().st_size == old.st_size
    updated = feed.read(sid, tmp_path, life, first["cursor"])
    assert team_events(updated)["route-01"]["status"] == "running"
    removed_id = team_events(updated)["route-01"]["id"]
    path.unlink()
    deleted = feed.read(sid, tmp_path, life, updated["cursor"])
    assert deleted["removed_event_ids"] == [removed_id]
    assert deleted["tasks"] == []
    assert "route-01" not in team_events(feed.read(sid, tmp_path, life))


def test_heartbeat_alone_does_not_invalidate_team_card_evidence(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    task_board.claim_top(board, "worker-1", now=20)
    task_board.heartbeat(board, "route-01", now=25)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    first_event = team_events(first)["route-01"]
    task_board.heartbeat(board, "route-01", now=35)
    heartbeat = feed.read(sid, tmp_path, life, first["cursor"])
    assert heartbeat["events"] == []
    assert heartbeat["cursor"] == first["cursor"]
    assert team_events(feed.read(sid, tmp_path, life))["route-01"]["revision"] == first_event["revision"]
    task_board.fail(board, "route-01", reason="Provider declined the request.")
    failed = feed.read(sid, tmp_path, life, first["cursor"])
    assert team_events(failed)["route-01"]["revision"] != first_event["revision"]
    assert team_events(failed)["route-01"]["reason"] == "Provider declined the request."


def test_selector_appears_only_when_its_real_board_exists(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    selector_board = board.with_name(board.name + "-selection")
    task_board.form(selector_board, [{
        "task_id": "evidence-selector", "role": "idea-selector", "title": "Select the supported route",
        "objective": "Read all independently reviewed reports and choose one route.",
    }])
    updated = feed.read(sid, tmp_path, life, first["cursor"])
    selector = team_events(updated)["evidence-selector"]
    assert selector["team_id"] == "research-team-selection"
    assert selector["team_role"] == "idea-selector"
    assert selector["deps"] == []  # The separate board records no scheduler deps.
    assert selector["status"] == "pending"
    assert len(updated["events"]) == 1


def test_team_parent_is_explicit_and_cannot_cross_session_workspaces(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    other_sid, other_life, other_board = sample(tmp_path, "s-other-team-map")
    append(other_life, {"type": "idea.portfolio.formed", "item_id": "parent", "team_root": str(board), "ts": 20})
    append(life, {"type": "idea.portfolio.formed", "item_id": "foreign-task", "team_root": str(other_board), "ts": 21})
    value = read_map(other_sid, tmp_path, other_life)
    assert len(team_events(value)) == 3
    assert all(event["ts"] == 10 for event in team_events(value).values())
    # No formation evidence means no guessed parent, even with one active macro.
    (life / "events.jsonl").write_text(json.dumps({"type": "life.mission.started", "item_id": "parent", "ts": 2}) + "\n")
    assert team_events(read_map(sid, tmp_path, life)) == {}


@pytest.mark.parametrize("escape", ["board", "tasks", "task_file", "teams_root"])
def test_team_projection_rejects_symlink_escapes(tmp_path: Path, escape: str, require_symlink_support) -> None:
    sid, life, board = sample(tmp_path)
    target = {
        "board": board,
        "tasks": board / "tasks",
        "task_file": board / "tasks" / "route-01.json",
        "teams_root": board.parent,
    }[escape]
    outside = tmp_path / "outside-team-data"
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    result = team_events(read_map(sid, tmp_path, life))
    assert "route-01" not in result
    if escape != "task_file":
        assert result == {}


def test_reparenting_removes_old_projection_and_repeated_formation_keeps_order(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent", "team_root": str(board), "ts": 30})
    repeated = feed.read(sid, tmp_path, life, first["cursor"])
    assert repeated["events"] == []
    LifeMemory.open(life).backlog.add(BacklogItem(id="repair", ts=40, title="Repair routes", objective="Repair", status="running"))
    append(life, {"type": "idea.portfolio.formed", "item_id": "repair", "team_root": str(board), "ts": 41})
    changed = feed.read(sid, tmp_path, life, first["cursor"])
    assert changed["removed_event_ids"] == sorted(event["id"] for event in team_events(first).values())
    assert all(event["item_id"] == "repair" for event in team_events(changed).values())


def test_team_bindings_survive_rotation_and_cold_read(tmp_path: Path) -> None:
    sid, life, board = sample(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    (life / "events.jsonl").rename(life / "events.jsonl.1")
    append(life, {"type": "round.start", "item_id": "parent", "ts": 30})
    assert len(team_events(feed.read(sid, tmp_path, life))) == 3
    assert len(team_events(read_map(sid, tmp_path, life))) == 3
    task_board.fail(board, "route-02", reason="Worker stopped.")
    changed = feed.read(sid, tmp_path, life, first["cursor"])
    assert team_events(changed)["route-02"]["reason"] == "Worker stopped."


def test_team_projection_bounds_record_count_and_json_size(tmp_path: Path, monkeypatch) -> None:
    sid, life, board = sample(tmp_path)
    monkeypatch.setattr(map_team, "MAX_TEAM_TASKS", 2)
    bounded = read_map(sid, tmp_path, life)
    assert len(team_events(bounded)) == 2
    assert bounded["coverage"]["truncated"]
    monkeypatch.setattr(map_team, "MAX_TEAM_TASKS", 512)
    path = board / "tasks" / "route-01.json"
    value = json.loads(path.read_text())
    value["objective"] = "x" * (map_team.MAX_TASK_BYTES + 1)
    _store.atomic_write_json(path, value)
    bounded = read_map(sid, tmp_path, life)
    assert "route-01" not in team_events(bounded)
    assert bounded["coverage"]["truncated"]


def test_history_api_includes_team_updates_and_deletions_on_unchanged_journal_cursor(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from argus_skill.webapi.server import create_app

    sid, life, board = sample(tmp_path)
    journal_before = (life / "events.jsonl").read_bytes()
    with TestClient(create_app(global_root=tmp_path)) as client:
        url = f"/api/projects/{sid}/map-history"
        first_response = client.get(url)
        assert first_response.status_code == 200
        first = first_response.json()
        assert len(team_events(first)) == 3
        assert first["team_events_complete"] is True
        params = {"after": first["history_cursor"], "task_after": first["cursor"]}
        same = client.get(url, params=params).json()
        assert same["events"] == same["tasks"] == []
        assert same["team_events_complete"] is False

        task_board.fail(board, "route-01", reason="Worker stopped before delivering its report.")
        updated = client.get(url, params=params).json()
        assert updated["incremental"] is True
        assert updated["history_cursor"] == first["history_cursor"]
        assert updated["cursor"] != first["cursor"]
        assert len(updated["events"]) == 1
        assert team_events(updated)["route-01"]["status"] == "failed"
        assert updated["team_events_complete"] is False
        removed = team_events(updated)["route-01"]["id"]
        (board / "tasks" / "route-01.json").unlink()
        deleted = client.get(url, params={**params, "task_after": updated["cursor"]}).json()
        assert deleted["removed_event_ids"] == [removed]
        assert deleted["events"] == []
        assert deleted["history_cursor"] == first["history_cursor"]

        # A forgotten/expired task cursor replaces Team observations without
        # resetting the independent, still-valid journal history cursor.
        full_team = client.get(url, params={**params, "task_after": "expired"}).json()
        assert full_team["incremental"] is True
        assert full_team["team_events_complete"] is True
        assert len(team_events(full_team)) == 2
        assert not any(event["type"] != "team.task" for event in full_team["events"])
        assert full_team["history_cursor"] == first["history_cursor"]
    assert (life / "events.jsonl").read_bytes() == journal_before


def test_history_team_projection_does_not_duplicate_journal_pages_or_replay_unchanged_tails(
    tmp_path: Path, monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    from argus_skill.webapi import map_history
    from argus_skill.webapi.server import create_app

    sid, life, _board = sample(tmp_path)
    for index in range(3):
        append(life, {"event_id": f"round-{index}", "type": "round.start", "item_id": "parent", "ts": 20 + index})
    monkeypatch.setattr(map_history, "PAGE_EVENTS", 1)
    with TestClient(create_app(global_root=tmp_path)) as client:
        url = f"/api/projects/{sid}/map-history"
        first = client.get(url).json()
        seen = [event["id"] for event in first["events"] if event["type"] != "team.task"]
        assert len(seen) == 1 and len(team_events(first)) == 3
        page = first
        # Once built, the full and tasks-only feed entries are reused while
        # the durable history index advances through its existing pages.
        with monkeypatch.context() as patch:
            patch.setattr("argus_skill.webapi.map_feed.read_map", lambda *a, **k: pytest.fail("unchanged tail reread"))
            for _ in range(5):
                page = client.get(url, params={"after": page["history_cursor"], "task_after": page["cursor"]}).json()
                assert team_events(page) == {}
                seen.extend(event["id"] for event in page["events"])
                if not page["history_loading"]:
                    break
            else:
                pytest.fail("history pages did not finish")
        assert len(seen) == len(set(seen)) == 4
        assert set(seen) >= {"round-0", "round-1", "round-2"}
        # Restarting journal pagination requires a full Team snapshot even
        # when the caller happens to retain a current task cursor.
        restarted = client.get(url, params={"task_after": page["cursor"]}).json()
        assert restarted["incremental"] is False
        assert restarted["team_events_complete"] is True
        assert len(team_events(restarted)) == 3
