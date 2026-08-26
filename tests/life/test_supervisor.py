from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest

from argus_skill.core.event_catalog import EventType
from argus_skill.core.pricing import usd_for_tokens
from argus_skill.core.transcript import read_turns
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    global_daily_spend,
)
from argus_skill.life.supervisor._constants import PLANNER_DEDUP_STATUSES


class _RecordingSink:
    """Captures events in memory. When ``life_dir`` is given it ALSO tees every
    event to ``<life_dir>/events.jsonl`` (verbosity="full") exactly like the
    daemon's ``JsonlEventSink`` — so a ``LifeMemory`` whose journal is an
    ``EventJournal`` (derived from that file) sees the events, matching how the
    real daemon persists them and mirroring the sibling life tests."""

    def __init__(self, life_dir: Any = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._tee = None
        if life_dir is not None:
            from argus_skill.life.event_log import JsonlEventSink

            self._tee = JsonlEventSink(None, life_dir=life_dir, verbosity="full")

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self._tee is not None:
            self._tee.handle_event(event)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "done"
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    research_result: dict[str, Any] | None = None


class _ScientistSpendRunner:
    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        prelude_context: str = "",
        scope: str = "",
        original_objective: str = "",
    ) -> _Outcome:
        sink.handle_event({
            "type": "skill.cost.completed",
            "agent_layer": "scientist",
            "matcher_model": "gpt-5.5",
            "distiller_model": "gpt-5.5-mini",
            "matcher": {
                "model": "gpt-5.5",
                "input_tokens": 200_000,
                "cached_input_tokens": 0,
                "output_tokens": 1_000,
            },
            "distiller": {
                "model": "gpt-5.5-mini",
                "input_tokens": 100_000,
                "cached_input_tokens": 50_000,
                "output_tokens": 2_000,
            },
            "usage_scope": "delta",
        })
        return _Outcome()


class _ResearchIncompleteRunner:
    def execute(self, **kwargs) -> _Outcome:
        return _Outcome(
            success=False,
            status="research_incomplete",
            stop_reason="doctoral target not reached",
        )


class _ResearchBreakthroughRunner:
    def execute(self, **kwargs) -> _Outcome:
        return _Outcome(
            research_result=_certified_research_result("verified_new_result"),
        )


class _MaintenanceRunner:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs) -> _Outcome:
        self.kwargs = kwargs
        outcome = _Outcome()
        outcome.final_review_status = "done"
        return outcome


class _ParallelRunner:
    def __init__(self, barrier: Barrier) -> None:
        self.barrier = barrier
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs) -> _Outcome:
        self.kwargs = kwargs
        self.barrier.wait(timeout=5)
        return _Outcome()


def test_primary_and_auxiliary_supervisors_run_disjoint_tasks_together(
    tmp_path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    for name in ("a", "b"):
        memory.backlog.add(BacklogItem.new(
            title=name,
            objective=f"write {name}",
            tags=["scope:bounded"],
            manager_decision={
                "routed": True,
                "vertical": "software",
                "workflow_mode": "direct",
            },
            parallel_safe=True,
            owns_paths=[f"outputs/{name}.txt"],
        ))
    barrier = Barrier(2)
    primary_runner = _ParallelRunner(barrier)
    helper_runner = _ParallelRunner(barrier)
    primary = LifeSupervisor(
        memory=memory,
        runner=primary_runner,
        sink=_RecordingSink(memory.root),
        config=LifeSupervisorConfig(
            project_worktree=tmp_path,
            worker_id="primary",
            coordinate_parallel_claims=True,
        ),
    )
    helper = LifeSupervisor(
        memory=memory,
        runner=helper_runner,
        sink=_RecordingSink(memory.root),
        config=LifeSupervisorConfig(
            project_worktree=tmp_path,
            parallel_worker=True,
            holds_stage_authority=False,
            worker_id="parallel-1",
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        primary_future = executor.submit(primary.tick)
        deadline = time.time() + 2
        while (
            not any(item.status == "running" for item in memory.backlog.all())
            and time.time() < deadline
        ):
            time.sleep(0.01)
        futures = [primary_future, executor.submit(helper.tick)]
        results = [future.result() for future in futures]

    assert all(result and result["status"] == "done" for result in results)
    assert primary_runner.kwargs["holds_stage_authority"] is True
    assert helper_runner.kwargs["holds_stage_authority"] is False


def test_crash_after_mission_claim_requeues_audit_and_reemits_started(
    tmp_path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(
        title="recover claimed mission",
        objective="finish after restart",
        tags=["scope:bounded"],
        manager_decision={
            "routed": True,
            "vertical": "software",
            "workflow_mode": "direct",
        },
    ))
    claimed = memory.backlog.claim_next()
    assert claimed is not None and claimed.id == item.id
    assert claimed.status == "running"

    sink = _RecordingSink(memory.root)
    restarted = LifeSupervisor(
        memory=memory,
        runner=_MaintenanceRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    assert sink.events[0]["type"] == "life.mission.requeued"
    assert sink.events[0]["item_id"] == item.id
    restarted._vertical_resolved = True
    restarted.tick()
    started = [
        event for event in sink.events
        if event["type"] == EventType.LIFE_MISSION_STARTED
    ]
    assert len(started) == 1
    assert started[0]["item_id"] == item.id


def test_framework_maintenance_uses_private_worktree_and_review(
    tmp_path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "project"
    project.mkdir()
    private = tmp_path / "private-framework"
    private.mkdir()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=project,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="repair framework",
        objective="fix observed defect",
        tags=["framework_maintenance", "review:required", "scope:bounded"],
        execution_workdir=str(private),
        manager_decision={
            "routed": True,
            "vertical": "argus_maintenance",
            "workflow_mode": "direct",
        },
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert result["review_status"] == "done"
    assert runner.kwargs["working_dir_override"] == str(private)
    assert runner.kwargs["maintenance_mission"] is True
    assert runner.kwargs["vertical_override"] == "argus_maintenance"
    assert runner.kwargs["require_independent_review"] is True
    assert runner.kwargs["allow_skill_changes"] is False


def test_skill_changes_require_explicit_mission_permission(tmp_path) -> None:
    from argus_skill.verticals._data_domain import (
        promote_data_domain,
        write_data_domain,
    )

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )
    write_data_domain(
        memory.root,
        "device_tuning",
        stages=("profile", "optimize"),
        status="candidate",
    )
    assert promote_data_domain(
        memory.root,
        memory.root,
        "device_tuning",
    )
    memory.backlog.add(BacklogItem.new(
        title="author reusable capability",
        objective="Create the explicitly requested reusable Skill.",
        tags=["planner", "scope:bounded", "skill_changes:allowed"],
        manager_decision={"routed": True, "vertical": "device_tuning"},
    ))
    supervisor._vertical_resolved = True

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert runner.kwargs["allow_skill_changes"] is True
    assert runner.kwargs["vertical_override"] == "device_tuning"
    assert supervisor._vertical_resolved is True


def test_candidate_vertical_executes_from_session_state_with_separate_worktree(
    tmp_path,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical
    from argus_skill.verticals._data_domain import write_data_domain

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "target-repo"
    project.mkdir()
    write_data_domain(
        memory.root,
        "embodied_eval_campaign",
        stages=["runtime_gate", "task_coverage", "evaluation"],
        status="candidate",
        purpose="RoboTwin runtime and paired evaluation",
        require_independent_review=True,
    )
    persist_vertical(
        memory.root,
        "embodied_eval_campaign",
        workflow_mode="staged",
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=memory.root,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="run candidate-domain mission",
        objective="exercise the project-local vertical",
        tags=["planner", "review:required", "scope:bounded"],
        manager_decision={
            "routed": True,
            "vertical": "embodied_eval_campaign",
            "workflow_mode": "staged",
            "learned_vertical_status": "candidate",
        },
    ))
    supervisor._vertical_resolved = True

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert runner.kwargs["vertical_override"] == "embodied_eval_campaign"
    assert not (
        project
        / "research"
        / "DOMAINS"
        / "embodied_eval_campaign.json"
    ).exists()


def test_stale_item_vertical_falls_back_without_unknown_vertical_crash(
    tmp_path,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "target-repo"
    project.mkdir()
    persist_vertical(memory.root, "software", workflow_mode="direct")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=memory.root,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="run stale routed mission",
        objective="execute despite stale route metadata",
        tags=["planner", "scope:bounded"],
        manager_decision={
            "routed": True,
            "vertical": "missing_candidate",
            "workflow_mode": "staged",
        },
    ))
    supervisor._vertical_resolved = True

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert runner.kwargs["vertical_override"] == ""


def test_manager_reselects_vertical_for_each_planned_mission(tmp_path) -> None:
    from argus_skill.manager.directive import set_active_manager_directive
    from argus_skill.skills.vertical_select import persist_vertical

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MaintenanceRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=tmp_path,
            artifact_root=tmp_path,
            continuous=True,
            continuous_objective="optimize the current project",
        ),
    )
    calls: list[str] = []

    class _Manager:
        selected = "device_tuning"

        def decide_vertical(self, objective: str):
            calls.append(objective)
            return SimpleNamespace(vertical=self.selected)

        @staticmethod
        def plan_stages(vertical: str) -> list[str]:
            assert vertical == "device_tuning"
            return ["profile", "optimize"]

        @staticmethod
        def commit_vertical_decision(
            objective,
            decision,
            *,
            ask_on_new_domain,
            force_stage_reset,
            _lock_held,
        ):
            assert objective == "optimize the current project"
            assert decision.vertical == "device_tuning"
            assert ask_on_new_domain is False
            assert force_stage_reset is True
            assert _lock_held is True
            return SimpleNamespace(
                vertical="device_tuning",
                domain="",
                kind="custom",
                workflow_mode="staged",
                learned_vertical_status="formal",
                stages=("profile", "optimize"),
            )

        @staticmethod
        def current_stage() -> str:
            return "profile"

    supervisor._bound_manager = lambda: _Manager()  # type: ignore[method-assign]
    supervisor._artifact_root = lambda: memory.root  # type: ignore[method-assign]
    persist_vertical(memory.root, "software", workflow_mode="direct")
    set_active_manager_directive(
        memory.root,
        "Build the Apple-specific inference kernel.",
    )

    first = supervisor._resolve_vertical_once()
    supervisor._vertical_resolved = False
    second = supervisor._resolve_vertical_once()

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0].startswith(
        "optimize the current project\n\n## Operator steering (standing)"
    )
    assert "Build the Apple-specific inference kernel." in calls[0]
    assert first["vertical"] == second["vertical"] == "device_tuning"


def test_regular_task_adopts_nested_repository_as_campaign_root(tmp_path) -> None:
    import subprocess

    from argus_skill.skills.vertical_select import persist_vertical, resolve_vertical

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    workspace = tmp_path / "workspace"
    target = workspace / "target-repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    (target / "user.bin").write_bytes(b"\x00user-owned\xff")
    before = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(target).parts
    }
    persist_vertical(memory.root, "software", workflow_mode="direct")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=workspace,
            artifact_root=memory.root,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="work in cloned repository",
        objective="make the bounded change",
        tags=["planner", "review:required", "scope:bounded"],
        execution_workdir="target-repo",
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert runner.kwargs["working_dir_override"] == str(target.resolve())
    assert runner.kwargs["maintenance_mission"] is False
    assert supervisor._project_workdir() == target.resolve()
    assert resolve_vertical(supervisor._artifact_root()) == "software"
    after = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(target).parts
    }
    assert after == before


def test_kernel_baseline_mission_receives_clean_reference_without_revert(
    tmp_path,
) -> None:
    import json
    import subprocess

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "kernel-project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project,
        check=True,
    )
    (project / "kernel.py").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "kernel.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=project, check=True)
    pipeline = project / ".argus" / "PIPELINE_STATE.json"
    pipeline.parent.mkdir()
    pipeline.write_text(
        json.dumps({
            "vertical": "kernel_engineering",
            "current_stage": "baseline",
        }),
        encoding="utf-8",
    )
    (project / "kernel.py").write_text("candidate\n", encoding="utf-8")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=project,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="capture baseline",
        objective="measure the clean reference",
        tags=["planner", "review:required", "scope:bounded"],
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    prelude = runner.kwargs["prelude_context"]
    assert "## Kernel baseline isolation" in prelude
    assert "clean_reference_root" in prelude
    assert (project / "kernel.py").read_text(encoding="utf-8") == "candidate\n"
    reference = memory.root / "runtime-worktrees" / "kernel-baseline" / "kernel.py"
    assert reference.read_text(encoding="utf-8") == "baseline\n"


def _certified_research_result(result_class: str) -> dict[str, Any]:
    return {
        "result_class": result_class,
        "correctness_status": "verified",
        "novelty_status": (
            "verified_new"
            if result_class == "verified_new_result"
            else "not_applicable"
        ),
        "statement_fidelity_status": "verified",
        "significance_status": (
            "doctoral"
            if result_class == "verified_new_result"
            else "exploratory"
        ),
        "evidence": ["independently checked evidence"],
        "limitations": [],
    }


def test_budget_pause_is_published_once_in_operator_chat(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchBreakthroughRunner(),
        sink=sink,
    )
    event = {
        "type": EventType.LIFE_BUDGET_PAUSE,
        "item_id": "task-1",
        "title": "Long experiment",
        "reason": "project daily budget exhausted",
    }

    assert sup._emit(event)
    assert sup._emit(event)

    (turn,) = read_turns(mem.root)
    assert "预算不足" in turn["text"]
    assert "Long experiment" in turn["text"]
    ui_events = [
        event
        for line in (mem.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("type") == "ui.argus"
    ]
    assert len(ui_events) == 1


def test_research_incomplete_mission_is_paused_and_resumable(tmp_path) -> None:
    assert "paused" in PLANNER_DEDUP_STATUSES
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchIncompleteRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(
        BacklogItem.new(title="doctoral research", objective="prove a new theorem")
    )

    result = sup.tick()

    assert result is not None
    assert result["success"] is False
    assert result["status"] == "research_incomplete"
    paused = next(
        (candidate for candidate in mem.backlog.all() if candidate.id == item.id),
        None,
    )
    assert paused is not None
    assert paused.status == "research_incomplete"
    assert paused.last_error == "doctoral target not reached"
    event = next(
        event
        for event in sink.events
        if event.get("type") == "life.mission.completed"
    )
    assert event["success"] is False
    assert event["resumable"] is True
    (experience,) = mem.failure_experiences.recent()
    assert experience.mission_id == item.id
    assert experience.status == "research_incomplete"
    assert "not a general impossibility" in experience.claim_boundaries[0]

    resumed = mem.backlog.resume_paused(item.id)
    assert resumed is not None
    assert resumed.status == "pending"
    assert resumed.attempt == 2


def test_skill_miss_scientist_spend_is_journaled(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    runner = _ScientistSpendRunner()
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(
            global_daily_cap_usd=0.0,
            max_missions=2,
        ),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    first = mem.backlog.add(BacklogItem.new(
        title="skill miss",
        objective="force a skill miss and distill",
    ))

    result = sup.tick()

    expected_scientist_usd = usd_for_tokens(
        "gpt-5.5",
        200_000,
        0,
        1_000,
    ) + usd_for_tokens("gpt-5.5-mini", 100_000, 50_000, 2_000)
    assert result is not None
    assert result["success"] is True
    completed = [entry for entry in mem.journal.all() if entry.kind == "mission_complete"]
    assert len(completed) == 1
    entry = completed[0]
    assert entry.cost_usd == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_cost_usd"] == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_input_tokens"] == 300_000
    assert entry.extra["input_tokens"] == 300_000
    assert mem.backlog.all()[0].id == first.id
    assert mem.backlog.all()[0].status == "done"


def _append_usage(project, call_id: str, completed_at: float, cost_usd: float) -> None:
    from argus_skill.core.usage import UsageLedger, UsageRecord

    project.mkdir(parents=True, exist_ok=True)
    UsageLedger(project, migrate_legacy=False).append(
        UsageRecord(
            call_id=call_id,
            project_id=project.name,
            mission_id=None,
            provider="test",
            model="",
            run_label="test.aggregate",
            started_at=completed_at,
            completed_at=completed_at,
            status="completed",
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_output_tokens=None,
            premium_requests=None,
            pricing_status="priced",
            pricing_tier="test",
            cost_usd=cost_usd,
            cost_basis="test",
        )
    )


def test_global_daily_spend_sums_across_projects_and_rollover(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)    )
    root = tmp_path / "root"
    _append_usage(root / "projects" / "p1", "old-p1", day_start - 1, 99.0)
    _append_usage(root / "projects" / "p1", "new-p1", day_start + 10, 1.25)
    _append_usage(root / "projects" / "p2", "new-p2", day_start + 20, 2.5)
    _append_usage(root / "projects" / "p2", "old-p2", day_start - 20, 7.0)

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.75)


def test_global_daily_spend_reads_canonical_usage_across_projects(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    root = tmp_path / "root"
    for project_id, call_id, cost, offset in (
        ("p1", "call-1", 1.25, 10),
        ("p2", "call-2", 2.5, 20),
        ("p3", "call-3", 3.75, 30),
    ):
        _append_usage(
            root / "projects" / project_id,
            call_id,
            day_start + offset,
            cost,
        )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(7.5)


def test_global_daily_spend_observes_new_cost_without_ttl_staleness(tmp_path) -> None:
    now = time.time()
    root = tmp_path / "root"
    project = root / "projects" / "p1"
    _append_usage(project, "first-call", now, 1.0)
    assert global_daily_spend(global_root=root, now=now) == pytest.approx(1.0)

    _append_usage(project, "new-call", now + 1, 2.0)

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.0)


def test_can_start_blocks_on_global_daily_cap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        lambda **_kwargs: 12.0,
    )
    budget = LifeBudget(global_daily_cap_usd=12.0)

    allowed, reason = budget.can_start(global_root=tmp_path, now=time.time())

    assert allowed is False
    assert "global daily budget exhausted" in reason


def test_global_daily_cap_zero_is_backward_compatible(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = LifeBudget(global_daily_cap_usd=0.0)
    calls = {"n": 0}

    def fake_global_daily_spend(**kwargs: Any) -> float:
        calls["n"] += 1
        return 999.0

    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        fake_global_daily_spend,
    )

    allowed, reason = budget.can_start(global_root=tmp_path, now=time.time())

    assert allowed is True
    assert reason == ""
    assert calls["n"] == 0


class _BudgetExhaustedRunner:
    """A runner whose provider call was denied by the host-global budget."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(success=False, status="budget_exhausted", final_message="paused")


def test_budget_exhausted_outcome_pauses_item_and_journals_budget_pause(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_BudgetExhaustedRunner(), sink=sink, config=cfg,
    )

    item = mem.backlog.add(BacklogItem.new(
        title="long mission",
        objective="something that reaches the host-global cap",
    ))

    result = sup.tick()

    # Hard pause, NOT a completion — reviewer stays the sole done-ness authority.
    assert result is not None
    assert result["status"] == "paused_budget"
    assert result["item_id"] == item.id
    assert result.get("success") is not True
    # Item is recoverably paused until an explicit resume starts a fresh attempt.
    rows = {row.id: row for row in mem.backlog.all()}
    assert rows[item.id].status == "paused_budget"
    # Exactly one budget_pause journal entry; no mission_complete.
    pauses = [e for e in mem.journal.all() if e.kind == "budget_pause"]
    assert len(pauses) == 1
    assert pauses[0].extra["item_id"] == item.id
    assert not [e for e in mem.journal.all() if e.kind == "mission_complete"]
    # A life.mission.completed event marks it as a non-success budget_pause.
    completed = [e for e in sink.events if e.get("type") == "life.mission.completed"]
    assert completed and completed[-1]["status"] == "paused_budget"
    assert completed[-1]["success"] is False


class _BlockedQuestionRunner:
    """A runner whose mission stops with a reviewer 'blocked' verdict carrying
    an operator_question — the shape apps/_runtime.py's real execute()
    produces (``_Outcome.operator_question``, extracted from the final
    round's ReviewDecision when ``status == "blocked"``)."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(
            success=False, status="blocked", final_message="needs a decision",
            operator_question="fp16 精度损失可以接受吗，还是必须 fp32？",
            operator_options=[
                {
                    "id": "allow-fp16",
                    "label": "允许 fp16",
                    "description": "接受精度损失并继续优化。",
                    "requires_note": False,
                },
                {
                    "id": "require-fp32",
                    "label": "必须 fp32",
                    "description": "保持 fp32 精度约束。",
                    "requires_note": False,
                },
            ],
        )


class _LateForbidQuestionRunner(_BlockedQuestionRunner):
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    def execute(self, **kwargs: Any) -> _Outcome:
        from argus_skill.manager.directive import set_active_manager_directive

        set_active_manager_directive(
            self.state_root,
            "continue without questions",
            operator_question_policy="forbid",
        )
        return super().execute(**kwargs)


def test_blocked_verdict_persists_operator_question_onto_backlog_item(
    tmp_path,
) -> None:
    """Point 11 of the 11-point CLI directive: the reviewer's operator_question
    must be durably visible, not just live in whatever cockpit process
    happened to be tailing events.jsonl at that instant. The supervisor is the
    ONE place every daemon mission outcome flows through,
    so this is where the question gets persisted onto the (now-terminal)
    backlog item for status views to read later."""
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_BlockedQuestionRunner(), sink=sink, config=cfg,
    )

    item = mem.backlog.add(BacklogItem.new(
        title="Optimize matmul kernel", objective="make it 2x faster",
    ))

    sup.tick()

    rows = {row.id: row for row in mem.backlog.all()}
    # Operator-paused rather than terminal: a failed dependency would
    # cascade-skip downstream DAG nodes before the answer can rewire them.
    assert rows[item.id].status == "paused_operator"
    assert rows[item.id].pending_question == "fp16 精度损失可以接受吗，还是必须 fp32？"
    assert rows[item.id].operator_decision["project_id"] == mem.root.name
    assert rows[item.id].operator_decision["options_source"] == "agent"
    assert [
        option["id"] for option in rows[item.id].operator_decision["options"]
    ] == ["allow-fp16", "require-fp32"]
    assert "campaign_generation" not in rows[item.id].operator_decision
    pending_events = [
        event
        for event in sink.events
        if event["type"] == "life.operator_question.pending"
    ]
    assert pending_events[-1]["item_id"] == item.id
    assert pending_events[-1]["question"] == "fp16 精度损失可以接受吗，还是必须 fp32？"


def test_late_forbid_prevents_custom_runner_question_from_parking(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    sup = LifeSupervisor(
        memory=mem,
        runner=_LateForbidQuestionRunner(mem.root),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Optimize matmul kernel",
        objective="make it 2x faster",
    ))

    result = sup.tick()

    assert result is not None
    assert result["status"] == "blocked"
    assert not result.get("operator_question")
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert stored.pending_question == ""
    assert stored.operator_decision == {}
    assert not any(
        event["type"] == "life.operator_question.pending"
        for event in sink.events
    )


class _TechnicalQuestionRunner:
    """A recoverable benchmark choice incorrectly phrased as a human question."""

    def execute(self, **kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            success=False,
            status="blocked",
            stop_reason="the largest benchmark row timed out",
            operator_question="Should the benchmark use a smaller diagnostic shape?",
        )
        outcome.final_review_reason = "The largest benchmark row timed out."
        outcome.final_review_next_action = (
            "Validate one smaller row, then replan the full measurement."
        )
        outcome.final_planner_report = {
            "forward_progress": False,
            "plan_signal": "reconsider",
            "authority_impact": "technical",
        }
        return outcome


def test_pragmatic_autonomy_replans_technical_question_without_pausing(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    monkeypatch.setenv("ARGUS_SKILL_AUTONOMY_MODE", "pragmatic")
    mem = LifeMemory.open(tmp_path / "life")
    set_active_manager_directive(
        mem.root,
        "continue without questions",
        operator_question_policy="forbid",
    )
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_TechnicalQuestionRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Measure the kernel", objective="compare baseline and candidate",
    ))

    result = sup.tick()

    assert result is not None and result["status"] == "replan_requested"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "pending"
    assert stored.pending_question == ""
    assert not stored.operator_decision
    decisions = [
        event
        for event in sink.events
        if event.get("type") == EventType.LIFE_MANAGER_PLAN_CHALLENGE_DECIDED
        and event.get("source") == "pragmatic_autonomy_policy"
    ]
    assert decisions
    assert decisions[-1]["authority_impact"] == "technical"


def test_pending_wait_status_is_not_repeated_across_supervisor_restarts(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    item = mem.backlog.add(BacklogItem.new(
        title="Choose a dataset", objective="run the baseline",
    ))
    mem.backlog.update(
        item.id,
        status="paused_operator",
        pending_question="Which dataset should the baseline use?",
    )
    config = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="finish the benchmark",
    )

    first_sink = _RecordingSink(mem.root)
    first = LifeSupervisor(
        memory=mem,
        runner=_BlockedQuestionRunner(),
        sink=first_sink,
        config=config,
    ).run()
    second_sink = _RecordingSink(mem.root)
    second = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"),
        runner=_BlockedQuestionRunner(),
        sink=second_sink,
        config=config,
    ).run()

    def _notices(sink) -> list[dict]:
        return [
            event for event in sink.events
            if "waiting for your answer" in json.dumps(event, default=str)
        ]

    # The question is raised once and not repeated on the next start. The run
    # no longer stops for it, so the notice is the only observable.
    assert _notices(first_sink)
    assert not _notices(second_sink)
    assert first["stopped_by"] != "pending_operator_question"
    assert second["stopped_by"] != "pending_operator_question"


def test_non_blocked_failure_does_not_set_pending_question(tmp_path) -> None:
    """A plain error/crash (status != "blocked") must never populate
    pending_question — it is specifically for "the reviewer needs YOU to
    decide something", not every failure."""

    class _CrashRunner:
        def execute(self, **kwargs: Any) -> _Outcome:
            return _Outcome(success=False, status="error", final_message="boom")

    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_CrashRunner(), sink=_RecordingSink(), config=cfg,
    )
    item = mem.backlog.add(BacklogItem.new(title="task", objective="x"))

    sup.tick()

    rows = {row.id: row for row in mem.backlog.all()}
    assert rows[item.id].status == "failed"
    assert rows[item.id].pending_question == ""


class _CountingReplanRunner:
    """Always returns ``replan_requested`` and counts every dispatch. Models a
    refuted node the Reviewer keeps sending back with no forward progress."""

    def __init__(self, stage_transition: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self._stage_transition = stage_transition

    def execute(self, **kwargs: Any) -> _Outcome:
        self.calls += 1
        outcome = _Outcome(
            success=False,
            status="replan_requested",
            stop_reason="reviewer refuted node; premise unsatisfiable",
        )
        if self._stage_transition is not None:
            outcome.stage_transition = self._stage_transition
        return outcome


class _ReplanQuestionRunner:
    """Requests a replacement plan that requires an operator decision first."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(
            success=False,
            status="replan_requested",
            stop_reason="the approved trust boundary is ambiguous",
            operator_question="May this function be added to the trusted boundary?",
        )


def test_replan_with_operator_question_uses_durable_answer_path(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_ReplanQuestionRunner(), sink=sink, config=cfg,
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Resolve boundary", objective="verify the reachable call chain",
    ))
    dependent = mem.backlog.add(BacklogItem.new(
        title="Continue proof",
        objective="verify the caller",
        deps=[item.id],
    ))

    result = sup.tick()

    assert result is not None
    assert result["status"] == "blocked"
    assert not [
        entry
        for entry in mem.journal.all()
        if entry.kind == "mission_replan_requested" and entry.id == item.id
    ]

    # The question survives a process restart and uses the existing atomic
    # answer-to-continuation transition.
    reopened = LifeMemory.open(tmp_path / "life")
    blocked = next(row for row in reopened.backlog.all() if row.id == item.id)
    assert blocked.status == "paused_operator"
    assert blocked.pending_question == (
        "May this function be added to the trusted boundary?"
    )
    assert reopened.backlog.resume_paused(item.id) is None
    assert reopened.backlog.resume_all_paused() == []
    assert reopened.backlog.next_pending() is None
    waiting = next(row for row in reopened.backlog.all() if row.id == dependent.id)
    assert waiting.status == "pending"
    original, continuation = reopened.backlog.continue_with_operator_reply(
        item.id,
        "No. Keep the trusted boundary unchanged.",
        manager_decision="Find a verified implementation without new assumptions.",
    )
    assert original is not None and original.status == "failed"
    assert original.pending_question == ""
    assert continuation is not None and continuation.status == "pending"
    rewired = next(row for row in reopened.backlog.all() if row.id == dependent.id)
    assert rewired.deps == [continuation.id]


def test_operator_question_pauses_its_item_not_the_campaign(tmp_path) -> None:
    """An unanswered question used to stop the whole run. Overnight that cost
    run-06 about twenty hours and run-04 about twelve, each sitting on one
    question with a half-written paper and plenty of work that did not need the
    answer. The item stays the operator's; the campaign keeps going.
    """
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="verify the project",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=_ReplanQuestionRunner(),
        sink=_RecordingSink(mem.root),
        config=cfg,
    )
    mem.backlog.add(BacklogItem.new(
        title="Resolve boundary", objective="verify the reachable call chain",
    ))

    planned: list[bool] = []

    def _plan_once(*args: Any, **kwargs: Any) -> bool:
        planned.append(True)
        return False

    sup._plan_next_work = _plan_once  # type: ignore[method-assign]

    result = sup.run()

    assert result["results"][0]["status"] == "blocked"
    assert result["stopped_by"] != "pending_operator_question"
    # The Planner is asked for work that does not depend on the answer.
    assert planned
    # And the question itself is still the operator's to answer.
    blocked = [
        item for item in mem.backlog.all()
        if str(getattr(item, "pending_question", "") or "").strip()
    ]
    assert blocked and blocked[0].status == "paused_operator"


def test_consecutive_replans_are_bounded_and_escalated(tmp_path, monkeypatch) -> None:
    """A plain ``replan_requested`` node must reset to pending and re-dispatch
    below the threshold, but once the consecutive-replan count reaches the
    threshold it must be escalated to a terminal no-progress failure that the
    planner quarantine recognizes — never re-dispatched again."""
    from argus_skill.life.supervisor._constants import (
        PLANNER_RECENT_FAILURE_STATUS,
    )
    from argus_skill.life.supervisor._helpers import (
        _is_recent_no_progress_failure,
    )

    monkeypatch.setenv(
        "ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD", "3"
    )
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    runner = _CountingReplanRunner()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=10),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)
    item = mem.backlog.add(BacklogItem.new(
        title="unsatisfiable node", objective="drain an impossible obligation",
    ))

    def _row():
        return {row.id: row for row in mem.backlog.all()}[item.id]

    # Below threshold: each replan resets the item to pending and re-dispatches.
    first = sup.tick()
    assert first is not None and first["status"] == "replan_requested"
    assert _row().status == "pending"
    assert runner.calls == 1

    second = sup.tick()
    assert second is not None and second["status"] == "replan_requested"
    assert _row().status == "pending"
    assert runner.calls == 2

    # At the threshold (3rd consecutive replan): escalate, do NOT reset.
    third = sup.tick()
    assert third is not None
    assert runner.calls == 3
    escalated = _row()
    assert escalated.status == "failed"
    assert escalated.status != "pending"

    # The journal carries a terminal no-progress failure the planner
    # quarantine recognizes (kind=mission_failed, terminal_status=no_progress).
    failed_entries = [
        e for e in mem.journal.all()
        if e.kind == "mission_failed" and e.id == item.id
    ]
    assert failed_entries
    last_failed = failed_entries[-1]
    assert last_failed.extra["terminal_status"] == PLANNER_RECENT_FAILURE_STATUS
    assert _is_recent_no_progress_failure(last_failed)
    # Exactly two replans were journaled before escalation converted the third.
    replans = [
        e for e in mem.journal.all()
        if e.kind == "mission_replan_requested" and e.id == item.id
    ]
    assert len(replans) == 2

    # Terminal: further ticks find nothing pending and never re-dispatch it.
    fourth = sup.tick()
    assert fourth is None
    assert runner.calls == 3
    assert _row().status == "failed"


def test_replan_after_forward_progress_is_not_redispatched(tmp_path) -> None:
    class _ProgressThenReplanRunner:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, **kwargs: Any) -> _Outcome:
            self.calls += 1
            outcome = _Outcome(
                success=False,
                status="replan_requested",
                stop_reason="bounded probe completed; redesign the next mission",
            )
            outcome.final_planner_report = {
                "forward_progress": True,
                "plan_signal": "reconsider",
            }
            return outcome

    mem = LifeMemory.open(tmp_path / "life")
    runner = _ProgressThenReplanRunner()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=_RecordingSink(mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="bounded probe",
        objective="measure the premise once, then choose replacement work",
    ))

    result = sup.tick()

    assert result is not None and result["status"] == "replan_requested"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert runner.calls == 1
    assert sup.tick() is None
    assert runner.calls == 1


def test_large_replan_threshold_uses_persisted_streak(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD",
        "60",
    )
    mem = LifeMemory.open(tmp_path / "life")
    runner = _CountingReplanRunner()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=_RecordingSink(mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=100),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="long replan streak",
        objective="eventually hit the configured convergence threshold",
    ))

    for _ in range(59):
        result = sup.tick()
        assert result is not None and result["status"] == "replan_requested"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.consecutive_replans == 59

    final = sup.tick()

    assert final is not None and final["status"] == "no_progress"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert runner.calls == 60


def test_stage_progress_resets_persisted_replan_streak(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD",
        "2",
    )

    class _ReplanStageReplanRunner:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, **kwargs: Any) -> _Outcome:
            self.calls += 1
            if self.calls == 2:
                outcome = _Outcome(success=True, status="done")
                outcome.stage_transition = {
                    "action": "advance",
                    "target_stage": "solve",
                }
                return outcome
            return _Outcome(
                success=False,
                status="replan_requested",
                stop_reason="revise this stage",
            )

    mem = LifeMemory.open(tmp_path / "life")
    runner = _ReplanStageReplanRunner()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=_RecordingSink(mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=10),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="progress between replans",
        objective="advance before revising the next stage",
    ))

    assert sup.tick()["status"] == "replan_requested"
    assert sup.tick()["status"] == "stage_continues"
    after_progress = next(row for row in mem.backlog.all() if row.id == item.id)
    assert after_progress.consecutive_replans == 0
    assert after_progress.replan_streak_tracked is True

    assert sup.tick()["status"] == "replan_requested"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "pending"
    assert stored.consecutive_replans == 1


def test_stage_reconciled_replan_is_untouched_by_convergence_guard(
    tmp_path, monkeypatch,
) -> None:
    """A ``replan_requested`` outcome that carries a Manager stage
    advance/rollback (``stage_reconciled_replan``) must keep its existing
    behavior — marked failed with the stage-reconciliation reason — and must
    NOT be rewritten by the consecutive-replan no-progress escalation, even at
    a threshold of 1."""
    monkeypatch.setenv(
        "ARGUS_SKILL_CONSECUTIVE_REPLAN_ESCALATION_THRESHOLD", "1"
    )
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    runner = _CountingReplanRunner(
        stage_transition={"action": "advance", "target_stage": "solve"},
    )
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=10),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)
    item = mem.backlog.add(BacklogItem.new(
        title="stage advance node", objective="advance the pipeline stage",
    ))

    sup.tick()

    row = {r.id: r for r in mem.backlog.all()}[item.id]
    assert row.status == "failed"
    # The stage-reconciliation reason, NOT the no-progress escalation reason.
    assert "manager advance" in (row.last_error or "")
    assert "consecutive replan_requested" not in (row.last_error or "")
    # Journaled as a normal replan_requested, not a no_progress mission_failed.
    replans = [
        e for e in mem.journal.all()
        if e.kind == "mission_replan_requested" and e.id == item.id
    ]
    assert len(replans) == 1
    no_progress = [
        e for e in mem.journal.all()
        if e.kind == "mission_failed"
        and e.id == item.id
        and e.extra.get("terminal_status") == "no_progress"
    ]
    assert not no_progress
