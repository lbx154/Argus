"""A runtime-owned team is external work the lead can wait on."""
from __future__ import annotations

import json
from pathlib import Path

from argus.engineer.external_work import (
    ExternalWorkState,
    inspect_external_work,
    render_external_work_advisory,
    scan_external_work,
)
from argus.engineer.round_waits import lead_auto_wait_enabled, runtime_team_wait_target
from argus.team import external_work as team_work
from argus.team import pool, registry, task_board

TEAM = "research-idea-pipeline-v8-g1"


def _team(project: Path, team_id: str = TEAM, *states: str, owner: str = "runtime") -> Path:
    root = project / ".argus" / "teams" / team_id
    specs = [
        {"task_id": f"{team_id}-route-{index:02d}", "title": f"route {index}", "objective": "o",
         "acceptance_check": "c", "owns_paths": [f"routes/{index}.md"]}
        for index in range(1, len(states) + 1)
    ]
    task_board.form(root, specs)
    for index, state in enumerate(states, 1):
        changes = {"state": state}
        if state in {"claimed", "running"}:
            changes.update(owner=f"w{index}", claim_ts=1_000.0, heartbeat_ts=1_500.0)
        if state == "failed":
            changes["reason"] = "provider refused"
        task_board._mutate(root, f"{team_id}-route-{index:02d}", **changes)
    registry.write_marker(project, team_id=team_id, team_root=root, cwd=project, now=900.0, owner=owner)
    return root


def test_running_team_is_waitable_external_work(tmp_path: Path) -> None:
    root = _team(tmp_path, TEAM, "running", "pending", "pending")
    pool.update(root, width=1, state="running")
    statuses = scan_external_work(tmp_path, now=2_000.0)
    assert [status.work_id for status in statuses] == [f"team:{TEAM}"]
    status = statuses[0]
    assert status.source == "team" and status.owner == "runtime" and status.waitable
    assert status.description == f"team {TEAM}: 1 running, 2 pending, 0 done"
    assert status.facts[0].startswith(f"{TEAM}-route-01: running by w1 for 16m")
    assert status.evidence_paths == (f".argus/teams/{TEAM}/artifacts",)
    assert inspect_external_work(tmp_path, f"team:{TEAM}", now=2_000.0) is not None
    advisory = render_external_work_advisory(tmp_path, now=2_000.0)
    assert f"`team:{TEAM}` (team): running_healthy" in advisory
    assert '"wait_for": "external_work"' in advisory


def test_pending_tasks_with_a_staffed_pool_are_patience_not_a_problem(tmp_path: Path) -> None:
    root = _team(tmp_path, TEAM, "pending", "pending")
    pool.update(root, width=1, state="running", cooldown_until=5_000.0)
    status = scan_external_work(tmp_path, now=2_000.0)[0]
    assert status.state is ExternalWorkState.RUNNING_HEALTHY


def test_finished_team_is_terminal(tmp_path: Path) -> None:
    _team(tmp_path, TEAM, "done", "done")
    status = scan_external_work(tmp_path, now=2_000.0)[0]
    assert status.state is ExternalWorkState.TERMINAL and status.outcome == "all tasks done"
    assert not status.waitable


def test_failed_task_with_nobody_running_needs_the_lead(tmp_path: Path) -> None:
    _team(tmp_path, TEAM, "failed", "done")
    status = scan_external_work(tmp_path, now=2_000.0)[0]
    assert status.state is ExternalWorkState.NEEDS_ATTENTION
    assert "1 task(s) failed" in status.reason
    assert status.facts[0] == f"{TEAM}-route-01: failed — provider refused"


def test_draining_pool_with_pending_work_needs_the_lead(tmp_path: Path) -> None:
    root = _team(tmp_path, TEAM, "pending", "done")
    pool.update(root, state="draining")
    status = scan_external_work(tmp_path, now=2_000.0)[0]
    assert status.state is ExternalWorkState.NEEDS_ATTENTION
    assert "pool is draining" in status.reason


def test_dissolved_team_and_empty_boards_do_not_appear(tmp_path: Path) -> None:
    root = _team(tmp_path, TEAM, "done")
    pool.update(root, state="dissolved")
    assert scan_external_work(tmp_path, now=2_000.0)[0].state is ExternalWorkState.TERMINAL
    empty = tmp_path / ".argus" / "teams" / "empty"
    empty.mkdir(parents=True)
    registry.write_marker(tmp_path, team_id="empty", team_root=empty, cwd=tmp_path, now=1.0)
    assert [s.work_id for s in scan_external_work(tmp_path, now=2_000.0)] == [f"team:{TEAM}"]


def test_only_runtime_owned_teams_are_auto_wait_targets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    monkeypatch.delenv("ARGUS_TEAM_LEAD_AUTO_WAIT", raising=False)
    _team(tmp_path, "figure-studio-1", "running", owner="lead")
    assert runtime_team_wait_target(tmp_path) is None
    _team(tmp_path, TEAM, "running")
    target = runtime_team_wait_target(tmp_path)
    assert target is not None and target.work_id == f"team:{TEAM}"
    assert team_work.RUNTIME_OWNER == "runtime"


def test_workers_and_operators_can_switch_the_lead_wait_off(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    monkeypatch.delenv("ARGUS_TEAM_LEAD_AUTO_WAIT", raising=False)
    assert lead_auto_wait_enabled()
    monkeypatch.setenv("ARGUS_TEAM_LEAD_AUTO_WAIT", "off")
    assert not lead_auto_wait_enabled()
    monkeypatch.delenv("ARGUS_TEAM_LEAD_AUTO_WAIT")
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", f"{TEAM}-route-01")
    assert not lead_auto_wait_enabled()


def test_the_portfolio_forms_its_teams_as_runtime_owned(tmp_path: Path) -> None:
    from argus.team import formation

    root = tmp_path / ".argus" / "teams" / TEAM
    formation.form_team(
        project_root=tmp_path, root=root, team_id=TEAM, mission="m", lead="engineer",
        cwd=tmp_path, tasks=[{"task_id": f"{TEAM}-route-01", "title": "r", "objective": "o", "acceptance_check": "c"}],
        owner=formation.RUNTIME_OWNER,
    )
    assert registry.list_markers(tmp_path)[0]["owner"] == "runtime"
    lead_root = tmp_path / ".argus" / "teams" / "figs"
    formation.form_team(
        project_root=tmp_path, root=lead_root, team_id="figs", mission="m", lead="lead",
        cwd=tmp_path, tasks=[{"task_id": "figs-1", "title": "r", "objective": "o", "acceptance_check": "c"}],
    )
    assert {m["team_id"]: m["owner"] for m in registry.list_markers(tmp_path)} == {TEAM: "runtime", "figs": "lead"}


def test_marker_pointing_nowhere_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".argus" / "team").mkdir(parents=True)
    (tmp_path / ".argus" / "team" / "ghost.json").write_text(json.dumps({
        "team_id": "ghost", "team_root": str(tmp_path / "nope"), "cwd": str(tmp_path), "created_ts": 1.0,
    }))
    assert scan_external_work(tmp_path, now=2.0) == []
