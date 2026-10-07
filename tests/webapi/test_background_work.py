from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.team import registry, task_board
from argus.webapi import project_state
from argus.webapi.background_work import background_work, waited_work_ids


def _board(workdir: Path, team_id: str = "routes") -> Path:
    board = workdir / ".argus" / "teams" / team_id
    task_board.form(board, [
        {"task_id": "route-01", "role": "idea-route", "title": "Investigate route one", "target": "route-01"},
        {"task_id": "route-02", "role": "idea-route", "title": "Investigate route two", "target": "route-02"},
        {"task_id": "route-03", "role": "idea-route", "title": "Investigate route three", "target": "route-03"},
        {"task_id": "route-01-review", "role": "idea-review", "title": "Review route one",
         "target": "route-01", "deps": ["route-01"]},
    ])
    registry.write_marker(workdir, team_id=team_id, team_root=board, cwd=workdir, now=100.0, owner="runtime")
    return board


def test_a_parked_mission_reports_the_team_it_waits_on_with_readable_counts(tmp_path: Path) -> None:
    board = _board(tmp_path)
    task_board.claim_top(board, "w1", now=110)
    task_board.complete(board, "route-01")
    # The review of route one is claimable now and sorts first.
    task_board.claim_top(board, "w2", now=120)
    task_board.heartbeat(board, "route-01-review", now=130)
    items = [{
        "id": "mission", "status": "paused_external_work",
        "outcome": {"execution_status": "paused", "external_wait": {"kind": "external_work", "work_id": "team:routes"}},
    }]

    [entry] = background_work(tmp_path, items, [], now=140)

    assert entry["work_id"] == "team:routes"
    assert entry["owner"] == "runtime"
    assert entry["waited_by"] == ["mission"]
    assert (entry["state"], entry["total"], entry["done"], entry["running"], entry["pending"]) == ("running", 4, 1, 1, 2)
    assert entry["parts"] == [
        {"role": "idea-route", "total": 3, "done": 1, "running": 0, "attention": 0},
        {"role": "idea-review", "total": 1, "done": 0, "running": 1, "attention": 0},
    ]
    # Completion stamps the wall clock; the heartbeat is the last recorded beat.
    finished = json.loads((board / "tasks" / "route-01.json").read_text())["finished_ts"]
    assert entry["last_progress_ts"] == max(130, min(finished, 140))
    assert entry["started_ts"] == 100


def test_an_in_round_wait_is_read_from_the_journal_tail_until_it_completes(tmp_path: Path) -> None:
    _board(tmp_path)
    items = [{"id": "mission", "status": "running", "started_ts": 100, "outcome": {}}]
    started = {"type": "round.external_work_wait.started", "item_id": "mission", "work_id": "team:routes", "ts": 150}
    assert waited_work_ids(items, [started]) == {"team:routes": ["mission"]}
    assert background_work(tmp_path, items, [started])[0]["waited_by"] == ["mission"]

    completed = {"type": "round.external_work_wait.completed", "item_id": "mission", "work_id": "team:routes", "ts": 160}
    assert waited_work_ids(items, [started, completed]) == {}
    # The team is still listed for a running mission, only nobody is recorded waiting on it.
    assert background_work(tmp_path, items, [started, completed])[0]["waited_by"] == []
    # A wait recorded before the attempt started belongs to an earlier attempt.
    assert waited_work_ids([{**items[0], "started_ts": 155}], [started]) == {}


def test_finished_projects_and_foreign_boards_cost_nothing(tmp_path: Path) -> None:
    _board(tmp_path)
    assert background_work(tmp_path, [{"id": "m", "status": "done", "outcome": {}}], []) == []
    assert background_work(None, [{"id": "m", "status": "running"}], []) == []
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    registry.write_marker(tmp_path, team_id="escape", team_root=outside, cwd=tmp_path, now=1.0)
    entries = background_work(tmp_path, [{"id": "m", "status": "running"}], [])
    assert [entry["team_id"] for entry in entries] == ["routes"]
    # A parked wait on a team without a marker still finds the board by its id.
    parked = [{"id": "m", "status": "paused_external_work",
               "outcome": {"external_wait": {"work_id": "team:routes"}}}]
    (tmp_path / ".argus" / "team" / "routes.json").unlink()
    assert background_work(tmp_path, parked, [])[0]["waited_by"] == ["m"]


def test_snapshot_carries_background_work_for_the_session_workdir(tmp_path: Path) -> None:
    sid = "s-bgwork"
    workdir = tmp_path / "workspaces" / sid
    workdir.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workdir), created=1))
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True, exist_ok=True)
    _board(workdir)
    (life / "backlog.jsonl").write_text(json.dumps({
        "id": "mission", "title": "Choose an idea", "objective": "Compare routes",
        "status": "paused_external_work", "priority": 100, "ts": time.time(),
        "outcome": {"execution_status": "paused", "review_status": "not_assessed",
                    "stage_certification": "not_assessed", "interruption_kind": "none",
                    "resumable": True,
                    "external_wait": {"kind": "external_work", "work_id": "team:routes", "workdir": str(workdir)}},
    }) + "\n", encoding="utf-8")
    (life / "events.jsonl").write_text("", encoding="utf-8")

    snapshot = project_state.build_snapshot(sid, global_root=tmp_path, compact=True)

    assert snapshot is not None
    assert not [d for d in snapshot["diagnostics"] if d["section"] == "background_work"]
    [entry] = snapshot["background_work"]
    assert entry["work_id"] == "team:routes" and entry["waited_by"] == ["mission"]
    assert entry["total"] == 4 and entry["done"] == 0
