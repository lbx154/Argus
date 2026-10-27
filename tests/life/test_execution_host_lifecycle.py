"""A missing Codex tool host parks the work it failed until an explicit repair/retry."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.models import ReviewDecision, RunnerResult
from argus.core.stop_kinds import OPERATOR_RESUMED_PARKS
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.life.memory import BacklogItem, LifeMemory, park_reason
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.reviewer import ReviewerConfig
from argus.skills.loop_settlement import MissionSettlementMixin

HOST_ERROR = (
    "Code Mode is unavailable because failed to spawn code-mode host "
    "C:/Codex/code-mode-host.exe: host executable was not found; fail closed"
)


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
                status="manager", reason=HOST_ERROR, next_action="Restore host",
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
            ),
            workdir=self.workdir,
            on_event=self.events.append,
        )
        self.rounds = rounds
        settler = MissionSettlementMixin()
        settler._emit = lambda _event: None
        loop = settler._settle_mission_outcome(
            SimpleNamespace(workdir=self.workdir), None, status, rounds, message, reason, None,
        )
        return SimpleNamespace(
            status=loop.status, success=status == "done", stop_reason=reason,
            final_message=message, rounds=len(rounds),
            stop_kind=loop.stop_kind, park_reason=loop.park_reason,
            final_review_status=rounds[-1].review.status,
            final_review_source="reviewer", stage_transition={},
        )


def _supervisor(memory: LifeMemory, runner: _MissionRunner) -> LifeSupervisor:
    return LifeSupervisor(
        memory=memory, runner=runner,
        sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=5),
            poll_interval_seconds=0,
        ),
    )


def test_host_failure_parks_only_the_task_it_failed_until_an_explicit_resume(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    first = memory.backlog.add(BacklogItem.new(title="first", objective="repair it"))
    second = memory.backlog.add(BacklogItem.new(title="second", objective="another task"))
    runner = _MissionRunner(tmp_path)

    result = _supervisor(memory, runner).tick()

    assert result is not None and result["status"] == "parked"
    assert result["success"] is False
    assert runner.backend.calls == 1
    assert runner.reviewer.calls == 0
    assert len(runner.rounds) == 1
    assert runner.rounds[0].fatal_error == HOST_ERROR
    assert any(event.get("review_skipped") for event in runner.events)
    assert not any(event.get("type") == "round.backend_failure.backoff" for event in runner.events)

    reopened = LifeMemory.open(memory.root)
    parked = next(row for row in reopened.backlog.all() if row.id == first.id)
    assert parked.status == "parked" and park_reason(parked) == "infra"
    assert parked.outcome["execution_status"] == "paused"
    assert parked.outcome["review_status"] == "not_assessed"
    assert parked.outcome["execution_host_failure"] == HOST_ERROR
    assert parked.outcome["resumable"] is True

    # Nothing the runtime rechecks ends it, and it holds no other work: the
    # next task runs and parks on the same missing host by itself.
    restarted = _supervisor(reopened, runner)
    assert restarted._resume_automatic_pauses() == []
    assert restarted.tick()["item_id"] == second.id
    assert runner.backend.calls == 2
    assert restarted.tick() is None
    assert runner.backend.calls == 2

    # The operator's explicit Run, once the host is restored, re-arms each
    # parked task with one fresh attempt.
    runner.backend.available = True
    resumed = reopened.backlog.resume_parked(OPERATOR_RESUMED_PARKS)
    assert [item.attempt for item in resumed] == [2, 2]
    assert restarted.tick()["success"] is True
    assert restarted.tick()["success"] is True
    assert runner.backend.calls == 4
    assert runner.reviewer.calls == 2
    assert runner.usage_ids == [
        f"{first.id}:attempt:1", f"{second.id}:attempt:1",
        f"{first.id}:attempt:2", f"{second.id}:attempt:2",
    ]


def test_reviewer_host_failure_parks_without_retrying_either_role(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(title="review", objective="review it"))
    runner = _MissionRunner(tmp_path)
    runner.backend.available = True
    runner.reviewer.fail_host = True

    result = _supervisor(memory, runner).tick()

    assert result["status"] == "parked"
    assert runner.backend.calls == runner.reviewer.calls == 1
    parked = next(row for row in memory.backlog.all() if row.id == item.id)
    assert parked.outcome["execution_host_failure"] == HOST_ERROR
    assert parked.outcome["review_status"] == "not_assessed"


@pytest.mark.parametrize("host_failure", [True, False])
def test_an_infrastructure_park_skips_the_stage_judge(
    tmp_path: Path, host_failure: bool,
) -> None:
    from argus.apps._runtime import _SkillLoopRunner
    from argus.apps._runtime_helpers import _ExecuteState
    from argus.core.models import LoopOutcome, RoundRecord
    from argus.engineer.round_stop_signals import execution_host_review_decision

    diagnostic = HOST_ERROR if host_failure else "ordinary infrastructure issue"
    review = execution_host_review_decision(fatal_error=diagnostic, exit_code=0)
    state = _ExecuteState()
    state.workdir = tmp_path
    state.mission_scope = "final_submission"
    state.effective_require_independent_review = True
    state.outcome = LoopOutcome(
        status="parked",
        park_reason="infra",
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

    assert (result.status, result.park_reason) == ("parked", "infra")
    assert result.stop_reason == diagnostic
    assert result.success is False
    assert result.final_submission_certified is False
    # A park never moves the stage; only a host failure drops the review credit.
    assert stage_calls == []
    assert result.final_review_status == ("not_assessed" if host_failure else "blocked")
    assert result.stage_transition == {}
