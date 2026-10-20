from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from argus.engineer.external_work import ExternalWorkState, ExternalWorkStatus
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import FILE_WAIT_CLIMB_SECONDS, PLAN_AWAITING
from argus.life.supervisor._planner_orchestration import PlannerOrchestrationMixin
from argus.life.supervisor._planning_context import PlanningContextMixin
from argus.life.supervisor._planning_cycle import PlanningCycleMixin
from argus.planner import PlannerVerdict, TaskSpec, WaitingContract
from argus.skills.vertical_select import persist_vertical
from argus.team import pool, registry, task_board


class _Runner:
    pass


class _NormalizationProbe(PlanningCycleMixin, PlanningContextMixin):
    def __init__(self, jobs: list[ExternalWorkStatus]) -> None:
        self.jobs = jobs
        self.events: list[dict] = []
        self._planning_cycles = 1

    def _project_workdir(self) -> Path:
        return Path("/nonexistent-for-this-test")

    def _waitable_subagent_jobs(self) -> list[ExternalWorkStatus]:
        return self.jobs

    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def _planner_waiting_observed_revision(self, **_kwargs) -> str:
        return "live-revision"


def _direct_job() -> ExternalWorkStatus:
    return ExternalWorkStatus(
        work_id="data-build",
        run_id="data-build-run-1",
        state=ExternalWorkState.RUNNING_HEALTHY,
        source="subagent",
    )


def _team_job() -> ExternalWorkStatus:
    return ExternalWorkStatus(
        work_id="team:research-idea-pipeline-v8-g1",
        state=ExternalWorkState.RUNNING_HEALTHY,
        source="team",
        owner="runtime",
        description="team research-idea-pipeline-v8-g1: 1 running, 2 pending, 3 done",
        facts=("research-idea-pipeline-v8-g1-route-02-review: running by w8 for 4m",),
        started_at=1.0,
    )


def test_a_wait_naming_the_team_by_its_bare_id_is_bound_to_it() -> None:
    """The Planner of s-009c3ec3 waited for 'research-idea-pipeline-v8-g1' and
    was degraded to a poll that re-ran it every backoff tick (2026-09-16)."""
    probe = _NormalizationProbe([_team_job()])

    bound = probe._bind_planner_wait(WaitingContract(
        recheck_condition="research-idea-pipeline-v8-g1 finishes its route reviews",
    ))

    assert bound.blocker_fingerprint == "live-subagents:team:research-idea-pipeline-v8-g1"
    assert bound.wake_on == ("subagent_state",)
    assert bound.observed_revision == "live-revision"


class _DigestProbe(PlannerOrchestrationMixin):
    def __init__(self, jobs: list[ExternalWorkStatus]) -> None:
        self.jobs = jobs

    def _waitable_subagent_jobs(self) -> list[ExternalWorkStatus]:
        return self.jobs


def test_digest_names_the_team_and_its_board_so_the_planner_need_not_read_it() -> None:
    line = _DigestProbe([_direct_job(), _team_job()])._live_subagent_id_line()
    assert "data-build, team:research-idea-pipeline-v8-g1" in line
    assert "live_team_status (team:research-idea-pipeline-v8-g1): team research-idea-pipeline-v8-g1: 1 running, 2 pending, 3 done" in line
    assert "route-02-review: running by w8 for 4m" in line
    assert "wait on the team id rather than re-reading its files" in line


def test_status_only_task_becomes_deterministic_event_wait() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="check it",
        new_tasks=[
            TaskSpec(
                title="Observe live data build",
                objective=(
                    "Run python -m argus.tools.subagent status "
                    "--task-id data-build. If it remains live, stop."
                ),
            )
        ],
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting is True
    assert normalized.new_tasks == []
    assert normalized.waiting_contract is not None
    assert normalized.waiting_contract.wake_on == ("subagent_state",)
    assert probe.events[-1]["type"] == "life.planner.external_poll_suppressed"


def test_a_wait_naming_live_work_gets_its_durable_identity() -> None:
    probe = _NormalizationProbe([_direct_job()])

    bound = probe._bind_planner_wait(WaitingContract(
        recheck_condition="observe only data-build; if still running, stop",
    ))

    assert bound.blocker_fingerprint == "live-subagents:data-build"
    assert bound.recheck_token == "data-build@data-build-run-1"
    assert bound.wake_on == ("subagent_state",)


def test_unrelated_contract_that_mentions_live_job_is_not_rewritten() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="operator approval is required before inspecting data-build artifacts",
        waiting=True,
        waiting_reason=(
            "operator approval is required before inspecting data-build artifacts"
        ),
        waiting_contract=WaitingContract(
            blocker_fingerprint="approval:artifact-inspection",
            recheck_condition="operator approves artifact inspection",
            recheck_token="approval-v1",
        ),
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_a_wait_that_names_no_live_work_is_not_bound_to_it() -> None:
    """Prose about a job is not a reference to it; only its id as a word is."""
    probe = _NormalizationProbe([_direct_job()])

    bound = probe._bind_planner_wait(WaitingContract(
        recheck_condition="the data build pipeline is rebuilt by the operator",
    ))

    assert bound.blocker_fingerprint == "unobserved"
    assert bound.wake_on == ("planner_input",)


def test_unrelated_uncontracted_wait_is_not_rewritten() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="operator credentials are required",
        waiting=True,
        waiting_reason="operator credentials are required",
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_independent_task_is_not_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="independent work",
        new_tasks=[
            TaskSpec(
                title="Implement parser",
                objective="Implement and test the manifest parser.",
            )
        ],
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_status_check_inside_independent_task_is_not_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="independent work",
        new_tasks=[
            TaskSpec(
                title="Implement parser while data build runs",
                objective=(
                    "Run argus.tools.subagent status --task-id data-build once, "
                    "if it remains live, independently implement and test the "
                    "manifest parser now."
                ),
            )
        ],
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_do_not_relaunch_prefix_does_not_discard_substantive_work() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="repair now",
        new_tasks=[
            TaskSpec(
                title="Repair manifest parser",
                objective=(
                    "Do not relaunch data-build. Repair and test the manifest "
                    "parser now."
                ),
            )
        ],
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_substantive_work_before_terminal_clause_is_not_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="repair now",
        new_tasks=[
            TaskSpec(
                title="Repair manifest parser",
                objective=(
                    "Repair and test the manifest parser for data-build now. "
                    "When it reaches terminal, validate its final receipt."
                ),
            )
        ],
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_analysis_before_terminal_clause_is_not_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="analyze now",
        new_tasks=[
            TaskSpec(
                title="Analyze data build artifacts",
                objective=(
                    "Analyze the existing data-build artifacts now. Once terminal, "
                    "validate its final receipt."
                ),
            )
        ],
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def test_job_id_in_title_and_pronoun_objective_is_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="observe only",
        new_tasks=[
            TaskSpec(
                title="Observe data-build",
                objective="Check its status; if it remains live, stop.",
            )
        ],
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting is True
    assert normalized.new_tasks == []


def test_operator_wait_that_mentions_live_job_is_not_suppressed() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="operator credentials are required to inspect data-build",
        waiting=True,
        waiting_reason="operator credentials are required to inspect data-build",
    )

    assert probe._normalize_live_subagent_wait(verdict) is verdict
    assert probe.events == []


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            continuous=True,
            continuous_objective="finish the durable data build",
            open_ended=False,
            final_certification_gate=False,
            project_worktree=project,
            artifact_root=life,
        ),
        planner_runner=object(),
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    return supervisor


def _write_direct_job(
    project: Path, *, state: str = "running", task_id: str = "data-build"
) -> Path:
    registry = project / ".argus_subagents"
    registry.mkdir(exist_ok=True)
    path = registry / f"{task_id}.json"
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "run_id": f"{task_id}-run-1",
                "mode": "direct",
                "state": state,
                "pid": os.getpid(),
                "worker_pid": os.getpid(),
                "started_at": 123.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _park_missions_with_dependents(supervisor: LifeSupervisor) -> list[BacklogItem]:
    project = supervisor._project_workdir()
    supervisor.config.coordinate_parallel_claims = True
    missions = []
    for task_id in ("blind-test-a", "blind-test-b"):
        _write_direct_job(project, task_id=task_id)
        item = supervisor.memory.backlog.add(BacklogItem.new(
            title=task_id,
            objective=f"Run and assess {task_id}",
            parallel_safe=True,
            owns_paths=[f"results/{task_id}"],
        ))
        supervisor.memory.backlog.update(
            item.id,
            status="paused_external_work",
            outcome={"external_wait": {"work_id": task_id, "workdir": str(project)}},
        )
        missions.append(item)
    analysis = supervisor.memory.backlog.add(BacklogItem.new(
        title="Assess blind tests",
        objective="Compare both completed blind tests",
        deps=[item.id for item in missions],
    ))
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Write conclusion",
        objective="Write the conclusion from the comparison",
        deps=[analysis.id],
    ))
    return missions


@pytest.mark.parametrize("terminal_state", ["done", "failed"])
@pytest.mark.parametrize("continuous", [True, False])
def test_parked_missions_wait_without_planner_and_resume_on_job_completion(
    tmp_path: Path, monkeypatch, terminal_state: str, continuous: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)
    supervisor.config.continuous = continuous
    missions = _park_missions_with_dependents(supervisor)
    calls = []

    def plan_next(_planner, **_kwargs):
        calls.append(True)
        return PlannerVerdict(
            project_done=False,
            reason="waiting for blind tests",
            waiting=True,
            waiting_contract=supervisor._live_subagent_event_wait_contract(
                supervisor._waitable_subagent_jobs()
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan_next)
    original = supervisor.memory.backlog.active()
    assert supervisor.memory.backlog.next_pending(respect_running=True) is None
    for _ in range(3):
        summary = supervisor.run()
        assert summary["stopped_by"] == PLAN_AWAITING
        assert summary["suggested_sleep"] > 0
    assert calls == []
    assert supervisor.memory.backlog.active() == original
    assert supervisor._idle_since is None
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    assert not any(event["type"] == "life.planner.start" for event in events)
    waits = [event for event in events if event["type"] == "life.planner.waiting"]
    if continuous:
        assert all(item.id in waits[-1]["reason"] for item in missions)

    restarted = _supervisor(project, life)
    restarted.config.continuous = continuous
    restarted.config.coordinate_parallel_claims = True
    assert restarted.run()["stopped_by"] == PLAN_AWAITING
    assert calls == []

    _write_direct_job(project, task_id="blind-test-a", state=terminal_state)
    executed = []

    def run_one(item):
        executed.append(item.id)
        restarted.memory.backlog.mark_done(item.id)
        return {"item_id": item.id, "status": "done", "success": True}

    monkeypatch.setattr(restarted, "_run_one", run_one)
    restarted.config.post_mission_hook = lambda _outcome: "test_mission_finished"
    assert restarted.run()["stopped_by"] == "test_mission_finished"
    assert executed == [missions[0].id]
    assert calls == []


def test_startable_work_still_runs_beside_parked_missions(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    _park_missions_with_dependents(supervisor)
    independent = supervisor.memory.backlog.add(BacklogItem.new(
        title="Implement independent parser",
        objective="Implement the parser without blind-test results",
        parallel_safe=True,
        owns_paths=["src/parser.py"],
    ))
    planning_context = []

    def plan_next(_planner, **kwargs):
        planning_context.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False,
            reason="prepare independent fixtures",
            new_tasks=[TaskSpec(
                title="Prepare parser fixtures",
                objective="Prepare fixtures without blind-test results",
                parallel_safe=True,
                owns_paths=["tests/fixtures"],
            )],
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan_next)
    supervisor._enter_pause_backoff()
    supervisor._enter_pause_backoff()
    original = supervisor.memory.backlog.active()
    assert supervisor._plan_next_work() is True
    assert len(planning_context) == 1
    assert "2 consecutive idle or paused cycle(s)" in planning_context[0]
    assert "concluding `waiting=true`" not in planning_context[0]
    for item in original:
        assert f"{item.status} task {item.id}: {item.title}; deps={item.deps}" in planning_context[0]
    executed = []

    def run_one(item):
        executed.append(item.id)
        supervisor.memory.backlog.mark_done(item.id)
        return {"item_id": item.id, "status": "done", "success": True}

    monkeypatch.setattr(supervisor, "_run_one", run_one)
    supervisor.config.post_mission_hook = lambda _outcome: "test_mission_finished"
    assert supervisor.run()["stopped_by"] == "test_mission_finished"
    assert executed == [independent.id]


def test_unchanged_live_job_skips_planner_across_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "0.001")
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    job_path = _write_direct_job(project)
    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="observe only",
            new_tasks=[
                TaskSpec(
                    title="Observe live data build",
                    objective=(
                        "Run python -m argus.tools.subagent status "
                        "--task-id data-build. If it remains live, stop."
                    ),
                )
            ],
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
    first = _supervisor(project, life)

    # The first turn is where the Planner proposed the status probe that got
    # suppressed. The second is the one turn granted per contract so it can act
    # on being told independent work is still schedulable -- nothing is queued
    # behind this wait, so the campaign's other mission slots are empty. From
    # the third on it is skipped again: one turn, not one per cycle.
    assert first._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert first._plan_next_work() == PLAN_AWAITING
    assert calls == 2
    assert first._plan_next_work() == PLAN_AWAITING
    assert calls == 2
    assert first._idle_since is None
    assert first._maybe_idle_timeout() == ""

    restarted = _supervisor(project, life)
    assert restarted._plan_next_work() == PLAN_AWAITING
    assert calls == 2, "the granted turn does not come back on restart"
    assert restarted._idle_since is None
    assert restarted._maybe_idle_timeout() == ""

    wait_state = json.loads(
        next(life.glob("planner-waiting-contract*.json")).read_text(encoding="utf-8")
    )
    assert wait_state["active"] is True
    assert wait_state["wake_on"] == ["subagent_state"]

    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["state"] = "done"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    assert restarted._plan_next_work() is True
    assert calls == 3


def test_repersisted_operator_event_wait_keeps_idle_turn_throttle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = 0

    def _contract() -> WaitingContract:
        return WaitingContract(
            blocker_fingerprint=(
                "submission_gate_closed_no_external_submission_authorized_8d2b840c"
            ),
            recheck_condition="operator authorizes venue submission",
            recheck_token="token-v1",
            wake_on=("authorization",),
            operator_action_required=True,
        )

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="venue submission needs operator authorization",
            waiting=True,
            waiting_reason="venue submission needs operator authorization",
            waiting_contract=_contract(),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    wait_path = next(life.glob("planner-waiting-contract*.json"))
    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    assert wait_state["idle_capacity_turn_used"] is True
    assert "idle_capacity_backlog_revision" in wait_state
    assert supervisor._planner_last_turn_signature

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2
    events = [
        json.loads(raw)
        for raw in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    planner_waiting = [
        event for event in events if event.get("type") == "life.planner.waiting"
    ]
    assert planner_waiting[-1]["model_call_skipped"] is True

    # A week of silence on the same inputs is not news.
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 7 * 86400)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    # A restart reads what the Planner last saw back from disk, so a daemon
    # that keeps restarting does not buy a call on unchanged inputs.
    restarted = _supervisor(project, life)
    assert restarted._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    monkeypatch.setattr(
        supervisor, "_current_planner_input_signature", lambda: "something-moved"
    )
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3, "the same changed state earns one turn, not one per cycle"

    monkeypatch.setattr(supervisor, "_waiting_backlog_revision", lambda: "changed")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 4


def test_non_operator_event_wait_never_regrants_on_turn_age(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="awaiting external authorization event",
            waiting=True,
            waiting_reason="awaiting external authorization event",
            waiting_contract=WaitingContract(
                blocker_fingerprint="authorization_event_wait_8d2b840c",
                recheck_condition="the authorization event arrives",
                recheck_token="token-v1",
                wake_on=("authorization",),
                operator_action_required=False,
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    # Elapsed time never grants a turn, however long the wait.
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 7 * 86400)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2


def test_wait_persistence_rejects_state_change_after_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    revisions = iter(["terminal"])
    monkeypatch.setattr(
        supervisor,
        "_planner_waiting_observed_revision",
        lambda **_kwargs: next(revisions),
    )
    contract = WaitingContract(
        blocker_fingerprint="live-subagents:abc",
        recheck_condition="data-build changes state",
        recheck_token="run-1",
        wake_on=("subagent_state",),
        observed_revision="live",
    )

    assert supervisor._persist_planner_waiting_contract(contract) is None
    assert list((tmp_path / "life").glob("planner-waiting-contract*.json")) == []


def test_a_wait_naming_live_work_persists_bound_to_its_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_direct_job(project)
    supervisor = _supervisor(project, tmp_path / "life")

    state = supervisor._persist_planner_waiting_contract(
        WaitingContract(recheck_condition="data-build changes state")
    )

    assert state is not None
    assert state["blocker_fingerprint"] == "live-subagents:data-build"
    assert state["wake_on"] == ["subagent_state"]
    assert state["observed_revision"]


def test_a_wait_naming_a_project_file_watches_it_and_climbs_after_a_while(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "results").mkdir(parents=True)
    (project / "results" / "output.json").write_text("{}", encoding="utf-8")
    supervisor = _supervisor(project, tmp_path / "life")

    before = time.time()
    state = supervisor._persist_planner_waiting_contract(
        WaitingContract(recheck_condition="`results/output.json` is rewritten.")
    )

    assert state is not None
    assert state["blocker_fingerprint"] == "artifacts:results/output.json"
    assert state["wake_on"] == ["artifact_revision"]
    assert state["watched_paths"] == ["results/output.json"]
    assert state["expires_at"] >= before + FILE_WAIT_CLIMB_SECONDS


def test_a_wait_the_host_cannot_observe_waits_on_the_planners_inputs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")

    state = supervisor._persist_planner_waiting_contract(WaitingContract(
        recheck_condition="the vendor publishes the new dataset",
        stage_reconciliation_required=True,
    ))

    assert state is not None
    assert state["blocker_fingerprint"] == "unobserved"
    assert state["wake_on"] == ["planner_input", "manager_stage"]
    # The Planner's inputs include this contract, so they are read after it is written.
    assert state["observed_revision"] == ""


def test_an_operator_wait_is_bound_to_authorization(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_direct_job(project)
    supervisor = _supervisor(project, tmp_path / "life")

    state = supervisor._persist_planner_waiting_contract(WaitingContract(
        recheck_condition="the operator approves running data-build again",
        operator_action_required=True,
    ))

    assert state is not None
    assert state["blocker_fingerprint"] == "operator"
    assert state["wake_on"] == ["authorization"]


def test_watched_paths_stay_confined_to_the_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    events: list[dict] = []
    supervisor._emit = lambda event: events.append(event) or True
    contract = WaitingContract(
        blocker_fingerprint="artifact:unsafe",
        recheck_condition="an external artifact changes",
        recheck_token="run-1",
        wake_on=("artifact_change",),
        watched_paths=("../outside.json",),
    )

    assert supervisor._persist_planner_waiting_contract(contract) is None
    assert any(
        event.get("error") == "planner wait has unsafe watched path"
        for event in events
    )
    assert list((tmp_path / "life").glob("planner-waiting-contract*.json")) == []


def test_team_wait_id_is_a_host_observed_event_source(tmp_path: Path) -> None:
    """A persisted Planner wait on `team:<id>` becomes an event wait that wakes
    when the team's projected state changes, not a bounded poll."""
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    team_id = "research-idea-pipeline-v8-g1"
    root = project / ".argus" / "teams" / team_id
    task_board.form(root, [{"task_id": f"{team_id}-route-01", "title": "r", "objective": "o", "acceptance_check": "c"}])
    # A running task's own heartbeat is what shows its teammate is alive.
    now = time.time()
    task_board._mutate(root, f"{team_id}-route-01", state="running", owner="w1", claim_ts=now, heartbeat_ts=now)
    pool.update(root, width=1, state="running")
    registry.write_marker(project, team_id=team_id, team_root=root, cwd=project, now=now, owner="runtime")

    contract = WaitingContract(recheck_condition=f"the route workers of {team_id} finish")
    state = supervisor._persist_planner_waiting_contract(contract)
    assert state is not None
    assert state["blocker_fingerprint"] == f"live-subagents:team:{team_id}"
    assert state["wake_on"] == ["subagent_state"]
    assert state["observed_revision"]
    rows = supervisor._external_work_state_rows(project)
    assert rows == [{"work_id": f"team:{team_id}", "run_id": "", "state": "running_healthy"}]

    task_board._mutate(root, f"{team_id}-route-01", state="done")
    assert supervisor._external_work_state_rows(project)[0]["state"] == "terminal"


def _park_one_mission(supervisor: LifeSupervisor) -> BacklogItem:
    project = supervisor._project_workdir()
    _write_direct_job(project, task_id="data-build")
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="data-build", objective="Run and assess data-build",
    ))
    supervisor.memory.backlog.update(
        item.id,
        status="paused_external_work",
        outcome={"external_wait": {"work_id": "data-build", "workdir": str(project)}},
    )
    return item


def test_the_extra_planner_turn_during_a_live_job_wait_is_granted_once(
    tmp_path: Path, monkeypatch,
) -> None:
    """With nothing queued behind a parked mission, the Planner is asked once
    when the wait starts and once more to schedule independent work. Persisting
    the same wait after that turn must not hand the turn out again: on
    2026-09-30 a campaign spent four Planner calls in two minutes that way."""
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)
    _park_one_mission(supervisor)
    calls: list[bool] = []

    def plan_next(_planner, **_kwargs):
        calls.append(True)
        return PlannerVerdict(
            project_done=False,
            reason="waiting for data-build",
            waiting=True,
            waiting_contract=supervisor._live_subagent_event_wait_contract(
                supervisor._waitable_subagent_jobs()
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan_next)
    for _ in range(6):
        assert supervisor.run()["stopped_by"] == PLAN_AWAITING
    assert len(calls) == 2
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    assert sum(event["type"] == "life.planner.start" for event in events) == 2
    assert any(event.get("model_call_skipped") for event in events if event["type"] == "life.planner.waiting")


def test_backlog_rewrites_without_scheduling_changes_are_not_new_evidence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    item = _park_one_mission(supervisor)
    before = supervisor._waiting_backlog_revision()
    assert before
    path = Path(supervisor.memory.root) / "backlog.jsonl"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert supervisor._waiting_backlog_revision() == before
    supervisor.memory.backlog.add(BacklogItem.new(title="Plot residuals", objective="Plot them"))
    after_add = supervisor._waiting_backlog_revision()
    assert after_add != before
    assert supervisor.memory.backlog.resume_paused(item.id) is not None
    assert supervisor._waiting_backlog_revision() not in {before, after_add}


def test_planner_operator_wait_with_empty_backlog_never_regrants_on_age(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The Planner's own operator-only wait, with nothing parked in the backlog,
    is the same "only the operator can move this" state as a parked question.
    It once re-granted a full Planner call every five minutes on unchanged
    inputs for as long as nobody answered. Changed inputs still earn the turn."""
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="the operator confirms the dataset licence",
            waiting=True,
            waiting_reason="the operator confirms the dataset licence",
            waiting_contract=WaitingContract(
                blocker_fingerprint="dataset-licence",
                recheck_condition="the operator confirms the dataset licence",
                recheck_token="dataset-licence-v1",
                wake_on=("authorization",),
                operator_action_required=True,
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)
    assert supervisor.memory.backlog.active() == []

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2
    now = time.time()
    for day in range(1, 13):
        monkeypatch.setattr(time, "time", lambda day=day: now + day * 86400)
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2, "an aged wait on unchanged inputs is not news"

    monkeypatch.setattr(
        supervisor, "_current_planner_input_signature", lambda: "something-moved"
    )
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3


def test_open_operator_question_with_unchanged_inputs_never_regrants_on_age(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """While the operator owes an answer, a timed regrant on unchanged inputs
    bought the same wait at model price every five minutes. Changed inputs
    still earn the turn."""
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = 0

    def _plan_next(_planner, **_kwargs):
        nonlocal calls
        calls += 1
        return PlannerVerdict(
            project_done=False,
            reason="the operator resolves the conflicting requirements",
            waiting=True,
            waiting_reason="the operator resolves the conflicting requirements",
            waiting_contract=WaitingContract(
                blocker_fingerprint="conflicting-requirements-wip",
                recheck_condition="the operator resolves the conflicting requirements",
                recheck_token="conflicting-requirements-wip",
                wake_on=("authorization",),
                operator_action_required=True,
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)
    parked = supervisor.memory.backlog.add(
        BacklogItem.new(title="Plan production", objective="plan production")
    )
    supervisor.memory.backlog.update(
        parked.id,
        status="paused_operator",
        pending_question="May the held work order continue?",
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2
    assert supervisor._planner_last_turn_signature

    now = time.time()
    for days in (1, 2, 3):
        monkeypatch.setattr(time, "time", lambda days=days: now + days * 86400)
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2, "an aged wait on unchanged inputs is not news"

    monkeypatch.setattr(
        supervisor, "_current_planner_input_signature", lambda: "something-moved"
    )
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3
