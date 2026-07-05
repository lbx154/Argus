"""External-blocker artifact discovery -- generic glob, no dated filenames."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from argus_skill.life.supervisor import (
    _operator_only_blocker_paths_for_project,
    _operator_only_external_blocker_wait_reason_for_project,
)
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.bot_state import BotState, save_bot_state


def _write_blocker(project_root: Path, filename: str, payload: dict) -> Path:
    diagnosis = project_root / "diagnosis"
    diagnosis.mkdir(parents=True, exist_ok=True)
    path = diagnosis / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_finds_legacy_dated_lock_file(tmp_path: Path):
    """Backwards-compat: the 3c40efa-era dated filename should still match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_lock_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "operator_only_external_blocker_lock_20260605.json"


def test_finds_undated_generic_filename(tmp_path: Path):
    """Forward-compat: new generic filename without date should also match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1


def test_returns_empty_when_no_blocker_file(tmp_path: Path):
    (tmp_path / "diagnosis").mkdir()
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_ignores_unrelated_diagnosis_files(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "stage_check_terminal_index.md").write_text("ignore me")
    (diagnosis / "operator_action_required.md").write_text("ignore me")
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_picks_most_recent_when_multiple(tmp_path: Path):
    import time

    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260601.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["a"],
        },
    )
    time.sleep(0.01)
    p2 = _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["b"],
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    # Most recent first.
    assert paths[0] == p2


def test_short_circuit_emits_waiting_without_calling_planner(
    tmp_path: Path,
    monkeypatch,
):
    """If an external blocker artifact is present, supervisor must not call
    planner.plan_next this cycle; it should emit a waiting decision."""
    monkeypatch.chdir(tmp_path)
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )
    from argus_skill.life.supervisor import LifeSupervisor

    short_circuit = LifeSupervisor._operator_external_blocker_short_circuit_decision(
        project_root=tmp_path,
    )
    assert short_circuit is not None
    assert getattr(short_circuit, "waiting", False) is True
    assert "operator-only" in (
        getattr(short_circuit, "waiting_reason", "")
        or getattr(short_circuit, "reason", "")
    )
    assert getattr(short_circuit, "task_count", 0) == 0


def test_short_circuit_returns_none_without_blocker(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    from argus_skill.life.supervisor import LifeSupervisor

    assert LifeSupervisor._operator_external_blocker_short_circuit_decision(
        project_root=tmp_path,
    ) is None


def test_plan_next_work_short_circuits_before_planner_runner(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )

    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()
    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    class _Runner:
        pass

    class _PlannerRunnerThatMustNotBeCalled:
        def run_exec(self, **_kwargs):  # pragma: no cover - proves no call
            raise AssertionError("planner runner should not be called")

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
        ),
        planner_runner=_PlannerRunnerThatMustNotBeCalled(),
    )

    result = sup._plan_next_work()

    assert result == "awaiting_external"
    assert any(e.get("type") == "life.planner.waiting" for e in events)
    assert mem.journal.tail(1)[0].kind == "planner_waiting"


def test_blocker_present_but_cooldown_elapsed_allows_one_wiki_collect(
    tmp_path: Path,
):
    project = tmp_path / "project"
    project.mkdir()
    init_wiki("demo", base=project)
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
        },
    )

    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()

    class _Sink:
        def handle_event(self, event: dict) -> None:
            pass

    class _Runner:
        pass

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
        ),
        planner_runner=None,
    )

    result = sup._plan_next_work()

    assert result is True
    pending = [item for item in mem.backlog.all() if item.status == "pending"]
    assert len(pending) == 1
    assert "wiki_collect" in pending[0].objective


def test_blocker_present_cooldown_not_elapsed_pure_skip(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    wiki = init_wiki("demo", base=project)
    save_bot_state(
        wiki / "data" / "bot_state.json",
        BotState(
            last_collected_at=datetime.now(timezone.utc),
            last_attempted_at=datetime.now(timezone.utc),
        ),
    )
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
        },
    )

    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()

    class _Sink:
        def handle_event(self, event: dict) -> None:
            pass

    class _Runner:
        pass

    class _PlannerRunnerThatMustNotBeCalled:
        def run_exec(self, **_kwargs):  # pragma: no cover
            raise AssertionError("planner runner should not be called")

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
        ),
        planner_runner=_PlannerRunnerThatMustNotBeCalled(),
    )

    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(sup._artifact_root(), "research")

    assert sup._plan_next_work() == "awaiting_external"
    assert mem.backlog.all() == []


def test_blocker_waits_until_all_targets_present(tmp_path: Path):
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/a.csv", "data/eval/b.csv"],
        },
    )
    (tmp_path / "data" / "eval").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "a.csv").write_text("ok\n", encoding="utf-8")

    reason = _operator_only_external_blocker_wait_reason_for_project(tmp_path)

    assert reason
    assert "b.csv" in reason


def test_blocker_resolves_when_all_targets_present(tmp_path: Path):
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/a.csv", "data/eval/b.csv"],
        },
    )
    (tmp_path / "data" / "eval").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "a.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "data" / "eval" / "b.csv").write_text("ok\n", encoding="utf-8")

    assert _operator_only_external_blocker_wait_reason_for_project(tmp_path) == ""


def test_malformed_blocker_json_is_treated_as_waiting(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "operator_only_external_blocker_x.json").write_text("{", encoding="utf-8")

    reason = _operator_only_external_blocker_wait_reason_for_project(tmp_path)

    assert reason
    assert "malformed JSON" in reason


def test_blocker_tmp_file_is_ignored(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "operator_only_external_blocker_x.json.tmp").write_text(
        "{",
        encoding="utf-8",
    )

    assert _operator_only_external_blocker_wait_reason_for_project(tmp_path) == ""


def test_bounded_mission_does_not_short_circuit_on_external_blocker(tmp_path: Path):
    """Regression: with ``full_emnlp_gate=False`` (a ``--bounded`` mission), an
    operator-only external blocker must NOT short-circuit the planner cycle.

    Otherwise a bounded diagnostic/survey mission can never reach
    ``project_done`` and waits forever on external artifacts it does not need.
    Here, with the short-circuit correctly bypassed and no planner runner
    wired, ``_plan_next_work`` falls through to the "no planner runner" path
    instead of emitting an ``awaiting_external`` waiting verdict.
    """
    project = tmp_path / "project"
    project.mkdir()
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )

    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()
    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    class _Runner:
        pass

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
            full_emnlp_gate=False,
        ),
        planner_runner=None,
    )

    result = sup._plan_next_work()

    # Short-circuit bypassed: NOT awaiting_external / planner_waiting.
    assert result != "awaiting_external"
    assert not any(e.get("type") == "life.planner.waiting" for e in events)
    tail = mem.journal.tail(1)
    assert not tail or tail[0].kind != "planner_waiting"
