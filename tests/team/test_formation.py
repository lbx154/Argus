from __future__ import annotations

from pathlib import Path

import pytest

from argus.team import _store, formation, roster, task_board


def _tasks() -> list[dict]:
    return [
        {
            "task_id": "implement",
            "title": "Implement",
            "objective": "Implement the change",
            "acceptance_check": "focused tests pass",
            "role": "implementer",
            "owns_paths": ["src/**"],
            "deps": [],
        },
        {
            "task_id": "review",
            "title": "Review",
            "objective": "Review the implementation",
            "acceptance_check": "review is clean",
            "role": "reviewer",
            "owns_paths": ["tests/**"],
            "deps": ["implement"],
        },
    ]


def _form(project: Path, root: Path, tasks: list[dict] | None = None) -> dict:
    return formation.form_team(
        project_root=project,
        root=root,
        team_id="team-1",
        mission="Ship the exact change",
        lead="lead",
        cwd=project,
        tasks=_tasks() if tasks is None else tasks,
        now=100.0,
    )


def test_receipt_written_before_roster_crash_recovers_exact_formation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    real_create = formation.roster.create

    def _crash(*args, **kwargs) -> None:
        raise RuntimeError("crash after receipt")

    monkeypatch.setattr(formation.roster, "create", _crash)
    with pytest.raises(RuntimeError, match="after receipt"):
        _form(project, root)

    receipt = formation.load_receipt(root)
    assert receipt["mission_objective"] == "Ship the exact change"
    assert roster.load(root) == {}

    monkeypatch.setattr(formation.roster, "create", real_create)
    recovered = _form(project, root)

    assert recovered["formation_id"] == receipt["formation_id"]
    assert roster.load(root)["mission_objective"] == "Ship the exact change"
    assert len(task_board.snapshot(root)) == 2


def test_receipt_written_before_roster_rejects_changed_mission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"

    def _crash(*args, **kwargs) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(formation.roster, "create", _crash)
    with pytest.raises(RuntimeError, match="crash"):
        _form(project, root)

    with pytest.raises(ValueError, match="dispatch receipt"):
        formation.form_team(
            project_root=project,
            root=root,
            team_id="team-1",
            mission="A different mission",
            lead="lead",
            cwd=project,
            tasks=_tasks(),
        )


def test_receipt_backed_partial_board_recovers_after_formation_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    real_form = formation.task_board.form

    def _partial_then_crash(team_root: Path, tasks: list[dict]) -> None:
        real_form(team_root, tasks[:1])
        raise RuntimeError("crash during board write")

    monkeypatch.setattr(formation.task_board, "form", _partial_then_crash)
    with pytest.raises(RuntimeError, match="board write"):
        _form(project, root)

    assert formation.load_receipt(root)["team_id"] == "team-1"
    assert len(task_board.snapshot(root)) == 1
    assert not (root / "pool.json").exists()

    monkeypatch.setattr(formation.task_board, "form", real_form)
    _form(project, root)

    assert len(task_board.snapshot(root)) == 2


def test_receiptless_exact_board_is_recovered_without_resetting_live_task(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    roster.create(
        root,
        team_id="team-1",
        mission="Ship the exact change",
        lead="lead",
        now=1.0,
    )
    task_board.form(root, _tasks())
    task_board.claim_top(root, "w1", now=2.0)

    _form(project, root)

    assert formation.load_receipt(root)["team_id"] == "team-1"
    implement = {
        task["task_id"]: task for task in task_board.snapshot(root)
    }["implement"]
    assert implement["state"] == "claimed"
    assert implement["owner"] == "w1"


def test_receiptless_legacy_board_recovers_with_defaulted_new_spec_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    legacy_tasks = [
        {key: value for key, value in task.items() if key != "role"}
        for task in _tasks()
    ]
    roster.create(
        root,
        team_id="team-1",
        mission="Ship the exact change",
        lead="lead",
        now=1.0,
    )
    task_board.form(root, legacy_tasks)
    for task in task_board.snapshot(root):
        task.pop("role", None)
        task.pop("non_goals", None)
        _store.atomic_write_json(
            task_board._path(root, task["task_id"]),
            task,
        )

    _form(project, root, legacy_tasks)

    assert formation.load_receipt(root)["team_id"] == "team-1"


def test_receiptless_partial_board_fails_closed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    roster.create(
        root,
        team_id="team-1",
        mission="Ship the exact change",
        lead="lead",
        now=1.0,
    )
    task_board.form(root, _tasks()[:1])

    with pytest.raises(ValueError, match="does not exactly match"):
        _form(project, root)

    assert formation.load_receipt(root) == {}
    assert len(task_board.snapshot(root)) == 1


def test_resume_rejects_full_child_spec_mismatch(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    _form(project, root)
    changed = _tasks()
    changed[0] = {
        **changed[0],
        "objective": "A changed objective",
        "acceptance_check": "a different check",
        "role": "reviewer",
        "owns_paths": ["other/**"],
        "deps": ["review"],
    }

    with pytest.raises(ValueError, match="dispatch receipt"):
        _form(project, root, changed)


def test_resume_preserves_completed_task_result_shard(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "team"
    _form(project, root)
    task_board.claim_top(root, "w1", now=1.0)
    task_board.complete(root, "implement", shard="shards/implement.jsonl")

    _form(project, root)

    implement = {
        task["task_id"]: task for task in task_board.snapshot(root)
    }["implement"]
    assert implement["state"] == "done"
    assert implement["result_shard"] == "shards/implement.jsonl"


def test_nested_team_formation_is_rejected_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "nested"
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "parent::route-01")

    with pytest.raises(RuntimeError, match="nested team formation is disabled"):
        _form(project, root)

    assert not root.exists()
    assert formation.registry.list_markers(project) == []


def test_explicit_nested_team_override_is_visible_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "nested"
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "parent::route-01")
    monkeypatch.setenv("ARGUS_SKILL_ALLOW_NESTED_TEAM", "1")

    assert _form(project, root)["team_id"] == "team-1"


def test_project_active_campaign_limit_fails_before_new_team_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ARGUS_TEAM_MAX_ACTIVE_CAMPAIGNS", "2")

    for index in range(2):
        formation.form_team(
            project_root=project,
            root=tmp_path / f"team-{index}",
            team_id=f"team-{index}",
            mission=f"mission-{index}",
            lead="lead",
            cwd=project,
            tasks=[{"task_id": f"task-{index}", "objective": "work"}],
        )

    rejected_root = tmp_path / "team-2"
    with pytest.raises(RuntimeError, match="active team campaigns"):
        formation.form_team(
            project_root=project,
            root=rejected_root,
            team_id="team-2",
            mission="mission-2",
            lead="lead",
            cwd=project,
            tasks=[{"task_id": "task-2", "objective": "work"}],
        )

    assert not rejected_root.exists()
    assert len(formation.registry.list_markers(project)) == 2


def test_team_task_count_limit_fails_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "oversized"
    monkeypatch.setenv("ARGUS_TEAM_MAX_TASKS_PER_FORMATION", "1")

    with pytest.raises(RuntimeError, match="above ARGUS_TEAM_MAX_TASKS_PER_FORMATION"):
        _form(project, root)

    assert not root.exists()
