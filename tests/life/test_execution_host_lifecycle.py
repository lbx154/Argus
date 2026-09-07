"""A missing Codex tool host parks work until an explicit repair/retry."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.core.runner_errors import is_execution_host_startup_error
from argus_skill.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_AWAITING
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.reviewer import ReviewerConfig

HOST_ERROR = (
    "Code Mode is unavailable because failed to spawn code-mode host "
    "C:/Codex/code-mode-host.exe: host executable was not found; fail closed"
)


@pytest.mark.parametrize("value", [
    None,
    "",
    "The code-mode host executable was not found",
    f"The log contains: {HOST_ERROR}",
    f'"{HOST_ERROR}"',
    "Code Mode is unavailable because failed to spawn code-mode host in the example",
    "Process exited with code 1 before turn completion.",
])
def test_host_receipt_requires_complete_runtime_diagnostic(value: object) -> None:
    assert not is_execution_host_startup_error(value)


class _Backend:
    def __init__(self) -> None:
        self.available = False
        self.calls = 0

    def run_exec(self, **_kwargs) -> RunnerResult:
        self.calls += 1
        if self.available:
            return RunnerResult(exit_code=0, agent_messages=["Tested the change."])
        # A later assistant message and a successful process exit cannot make
        # a trusted host startup receipt a valid tool-backed execution.
        return RunnerResult(
            exit_code=0,
            agent_messages=["The task is done."],
            fatal_error=HOST_ERROR,
            stop_kind="backend_unavailable",
        )


class _Reviewer:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend
        self.calls = 0
        self.fail_host = False

    def evaluate(self, **_kwargs) -> ReviewDecision:
        assert self.backend.available, "host-failed Engineer must skip review"
        self.calls += 1
        if self.fail_host:
            return ReviewDecision(
                status="blocked", reason=HOST_ERROR, next_action="Restore host",
                backend_unavailable=True,
                backend_fatal_error=HOST_ERROR, backend_stop_kind="backend_unavailable",
            )
        return ReviewDecision(status="done", reason="Verified the change.", next_action="")


class _MissionRunner:
    """Exercise the real round loop inside the lifecycle's execute boundary."""
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.backend = _Backend()
        self.reviewer = _Reviewer(self.backend)
        self.usage_ids: list[str] = []
        self.events: list[dict] = []
        self.rounds = []

    def execute(self, **kwargs):
        self.usage_ids.append(kwargs["usage_mission_id"])
        engine = SupervisedEngineer(
            engineer_runner=self.backend,
            reviewer=self.reviewer,
            engineer_config=EngineerConfig(model="test"),
            reviewer_config=ReviewerConfig(model="test"),
        )
        status, rounds, message, reason, _thread = engine.run(
            objective="repair the code",
            engineer_prompt_builder=lambda _next, _static=True: "repair it",
            supervised_config=SupervisedConfig(
                max_rounds=10,
                backend_failure_backoff_seconds=0,
                background_subagent_advisory=False,
            ),
            workdir=self.workdir,
            on_event=self.events.append,
        )
        self.rounds = rounds
        return SimpleNamespace(
            status=status, success=status == "done", stop_reason=reason,
            final_message=message, rounds=len(rounds),
            stop_kind=rounds[-1].stop_kind,
            final_review_status=rounds[-1].review.status,
            final_review_source="reviewer", stage_transition={},
        )


def _supervisor(memory: LifeMemory, runner: _MissionRunner) -> LifeSupervisor:
    return LifeSupervisor(
        memory=memory, runner=runner,
        sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=100.0, max_missions=5),
            poll_interval_seconds=0,
        ),
    )


def test_host_failure_persists_across_supervisors_and_explicit_retry(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    first = memory.backlog.add(BacklogItem.new(title="first", objective="repair it"))
    second = memory.backlog.add(BacklogItem.new(title="second", objective="another task"))
    runner = _MissionRunner(tmp_path)

    result = _supervisor(memory, runner).tick()

    assert result is not None and result["status"] == "infra_blocked"
    assert result["success"] is False
    assert runner.backend.calls == 1
    assert runner.reviewer.calls == 0
    assert len(runner.rounds) == 1
    assert runner.rounds[0].fatal_error == HOST_ERROR
    assert any(event.get("review_skipped") for event in runner.events)
    assert not any(event.get("type") == "round.backend_failure.backoff" for event in runner.events)

    reopened = LifeMemory.open(memory.root)
    parked = next(row for row in reopened.backlog.all() if row.id == first.id)
    assert parked.status == "infra_blocked"
    assert parked.outcome["execution_status"] == "paused"
    assert parked.outcome["review_status"] == "not_assessed"
    assert parked.outcome["execution_host_failure"] == HOST_ERROR
    assert parked.outcome["resumable"] is True

    restarted = _supervisor(reopened, runner)
    assert restarted._resume_automatic_pauses() == []
    for _ in range(3):
        blocked = restarted.tick()
        assert blocked["status"] == "infra_blocked"
        assert blocked["item_id"] == second.id
        assert blocked["blocked_item_id"] == first.id
    assert runner.backend.calls == 1
    assert reopened.backlog.next_pending().id == second.id
    assert restarted._pc_preflight_shortcircuits(_PlanCycleState(None)) == PLAN_AWAITING

    # An explicit retry permits precisely one new attempt. If the dependency
    # is still missing, the same persisted mission immediately parks again.
    resumed = reopened.backlog.resume_paused(first.id)
    assert resumed is not None and resumed.attempt == 2
    retry = restarted.tick()
    assert retry["status"] == "infra_blocked"
    assert runner.backend.calls == 2
    assert restarted.tick()["blocked_item_id"] == first.id
    assert runner.backend.calls == 2

    runner.backend.available = True
    assert reopened.backlog.resume_paused(first.id).attempt == 3
    assert restarted.tick()["success"] is True
    assert restarted.tick()["success"] is True
    assert runner.backend.calls == 4
    assert runner.reviewer.calls == 2
    assert runner.usage_ids[:3] == [f"{first.id}:attempt:{n}" for n in (1, 2, 3)]


def test_reviewer_host_failure_parks_without_retrying_either_role(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(title="review", objective="review it"))
    runner = _MissionRunner(tmp_path)
    runner.backend.available = True
    runner.reviewer.fail_host = True

    result = _supervisor(memory, runner).tick()

    assert result["status"] == "infra_blocked"
    assert runner.backend.calls == runner.reviewer.calls == 1
    parked = next(row for row in memory.backlog.all() if row.id == item.id)
    assert parked.outcome["execution_host_failure"] == HOST_ERROR
    assert parked.outcome["review_status"] == "not_assessed"


def test_ordinary_infrastructure_pause_does_not_hold_other_work(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    parked = BacklogItem.new(title="unrelated", objective="wait")
    parked.status = "infra_blocked"
    parked.last_error = HOST_ERROR  # Unstructured prose is not a host circuit.
    parked.outcome = {"interruption_kind": "backend_unavailable", "resumable": True}
    memory.backlog.add(parked)
    memory.backlog.add(BacklogItem.new(title="ready", objective="do work"))
    runner = _MissionRunner(tmp_path)
    runner.backend.available = True

    result = _supervisor(memory, runner).tick()

    assert result["success"] is True
    assert runner.backend.calls == runner.reviewer.calls == 1


@pytest.mark.parametrize("host_failure", [True, False])
def test_runtime_host_pause_skips_stage_judge_and_preserves_other_infra_outcomes(
    tmp_path: Path, host_failure: bool,
) -> None:
    from argus_skill.apps._runtime import _SkillLoopRunner
    from argus_skill.apps._runtime_helpers import _ExecuteState
    from argus_skill.core.models import LoopOutcome, RoundRecord
    from argus_skill.engineer.round_stop_signals import execution_host_review_decision

    diagnostic = HOST_ERROR if host_failure else "ordinary infrastructure issue"
    review = execution_host_review_decision(fatal_error=diagnostic, exit_code=0)
    state = _ExecuteState()
    state.workdir = tmp_path
    state.mission_scope = "final_submission"
    state.effective_require_independent_review = True
    state.outcome = LoopOutcome(
        status="infra_blocked",
        reason=diagnostic,
        rounds=[RoundRecord(1, "work output", 0, review, diagnostic, "backend_unavailable")],
        final_message="work output",
        workdir=str(tmp_path),
        stop_kind="backend_unavailable",
        recoverable=True,
    )
    runtime = _SkillLoopRunner.__new__(_SkillLoopRunner)
    runtime._consume_auth_failure = lambda: False
    runtime._set_usage_context = lambda _value: None
    stage_calls = []

    def stage_judge(**_kwargs):
        stage_calls.append(True)
        return {"action": "hold"}

    runtime._decide_stage_transition = stage_judge
    runtime._extract_execute_outcome_fields(state)
    runtime._maybe_decide_stage_transition(
        state, sink=SimpleNamespace(handle_event=lambda _event: None),
        mission_id="host-mission", usage_mission_id="host-mission:attempt:1",
        maintenance_mission=False, skip_stage_transition=False,
        preplanned=False, stage_closing=True,
    )
    result = runtime._build_execute_outcome(state)

    assert result.status == "infra_blocked"
    assert result.stop_reason == diagnostic
    assert result.success is False
    assert result.final_submission_certified is False
    assert len(stage_calls) == (0 if host_failure else 1)
    assert result.final_review_status == ("not_assessed" if host_failure else "blocked")
    assert result.stage_transition == ({} if host_failure else {"action": "hold"})


def test_planner_host_pause_gate_uses_split_memory_project_root(tmp_path: Path) -> None:
    from argus_skill.daemon.state import write_continuous_config

    global_root = tmp_path / "global"
    project_root = global_root / "projects" / "project-a"
    write_continuous_config(
        global_root, enabled=True, objective="unrelated global campaign",
    )
    write_continuous_config(
        project_root, enabled=False, objective="project campaign", done_reason=HOST_ERROR,
    )
    memory = LifeMemory.open(project_root)
    item = memory.backlog.add(BacklogItem.new(title="pending", objective="project work"))
    runner = _MissionRunner(tmp_path)
    supervisor = _supervisor(memory, runner)
    supervisor.memory = SimpleNamespace(
        root=global_root, project_root=project_root, backlog=memory.backlog,
    )

    blocked = supervisor._execution_host_failure_block(item=item)

    assert blocked is not None and blocked["status"] == "infra_blocked"
    assert "re-enable continuous work" in blocked["reason"]
    assert runner.backend.calls == 0
    write_continuous_config(project_root, enabled=True, objective="project campaign")
    assert supervisor._execution_host_failure_block(item=item) is None

    # An unrelated global pause cannot hold this enabled project.
    write_continuous_config(
        global_root, enabled=False, objective="global campaign", done_reason=HOST_ERROR,
    )
    assert supervisor._execution_host_failure_block(item=item) is None
