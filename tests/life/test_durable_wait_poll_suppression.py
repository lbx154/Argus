from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.engineer.external_work import ExternalWorkState, ExternalWorkStatus
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    IDLE_BACKOFF_CAP_SECONDS,
    OPERATOR_WAIT_TURN_REGRANT_SECONDS,
    PLAN_AWAITING,
)
from argus_skill.life.supervisor._planning_cycle import PlanningCycleMixin
from argus_skill.planner import PlannerVerdict, TaskSpec, WaitingContract
from argus_skill.skills.vertical_select import persist_vertical


class _Runner:
    pass


class _NormalizationProbe(PlanningCycleMixin):
    def __init__(self, jobs: list[ExternalWorkStatus]) -> None:
        self.jobs = jobs
        self.events: list[dict] = []
        self._planning_cycles = 1

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


def test_status_only_task_becomes_deterministic_event_wait() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="check it",
        new_tasks=[
            TaskSpec(
                title="Observe live data build",
                objective=(
                    "Run python -m argus_skill.tools.subagent status "
                    "--task-id data-build. If it remains live, stop."
                ),
            )
        ],
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting is True
    assert normalized.new_tasks == []
    assert normalized.waiting_contract is not None
    assert normalized.waiting_contract.wait_mode == "event"
    assert normalized.waiting_contract.wake_on == ("subagent_state",)
    assert probe.events[-1]["type"] == "life.planner.external_poll_suppressed"


def test_uncontracted_live_job_wait_gets_persistent_event_identity() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="observe only data-build; check its status and, if still running, stop",
        waiting=True,
        waiting_reason=(
            "observe only data-build; check its status and, if still running, stop"
        ),
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting_contract is not None
    assert normalized.waiting_contract.blocker_fingerprint.startswith(
        "live-subagents:"
    )
    assert normalized.waiting_contract.recheck_token
    assert normalized.waiting_contract.wake_on == ("subagent_state",)


def test_poll_mode_live_job_wait_is_upgraded_to_event_wait() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="observe only data-build; check its status and, if still running, stop",
        waiting=True,
        waiting_reason=(
            "observe only data-build; check its status and, if still running, stop"
        ),
        waiting_contract=WaitingContract(
            blocker_fingerprint="data-build",
            recheck_condition="check data-build again",
            recheck_token="run-1",
        ),
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting_contract is not None
    assert normalized.waiting_contract.wait_mode == "event"
    assert normalized.waiting_contract.wake_on == ("subagent_state",)
    assert probe.events[-1]["source"] == "poll_wait_contract"


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


def test_model_event_wait_is_rebound_to_host_observed_revision() -> None:
    probe = _NormalizationProbe([_direct_job()])
    verdict = PlannerVerdict(
        project_done=False,
        reason="data-build is still running",
        waiting=True,
        waiting_reason="data-build is still running",
        waiting_contract=WaitingContract(
            blocker_fingerprint="data-build",
            recheck_condition="check data-build again",
            recheck_token="run-1",
            wait_mode="event",
            wake_on=("subagent_state",),
        ),
    )

    normalized = probe._normalize_live_subagent_wait(verdict)

    assert normalized.waiting_contract is not None
    assert normalized.waiting_contract.observed_revision == "live-revision"
    assert probe.events[-1]["source"] == "unvalidated_event_wait_contract"


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
                    "Run argus_skill.tools.subagent status --task-id data-build once, "
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


def _write_direct_job(project: Path, *, state: str = "running") -> Path:
    registry = project / ".argus_subagents"
    registry.mkdir(exist_ok=True)
    path = registry / "data-build.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "data-build",
                "run_id": "data-build-run-1",
                "mode": "direct",
                "state": state,
                "worker_pid": os.getpid(),
                "started_at": 123.0,
            }
        ),
        encoding="utf-8",
    )
    return path


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
                        "Run python -m argus_skill.tools.subagent status "
                        "--task-id data-build. If it remains live, stop."
                    ),
                )
            ],
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
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
        next(life.glob("planner-waiting-contract-*.json")).read_text(encoding="utf-8")
    )
    assert wait_state["active"] is True
    assert wait_state["wait_mode"] == "event"
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
            wait_mode="event",
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

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    wait_path = next(life.glob("planner-waiting-contract-*.json"))
    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    assert wait_state["idle_capacity_turn_used"] is True
    assert "idle_capacity_turn_ts" in wait_state
    assert "idle_capacity_backlog_revision" in wait_state

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

    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    wait_state["idle_capacity_turn_ts"] = (
        time.time() - OPERATOR_WAIT_TURN_REGRANT_SECONDS - 1
    )
    supervisor._write_planner_waiting_contract_state(wait_state)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3

    monkeypatch.setattr(supervisor, "_waiting_backlog_revision", lambda: "changed")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 4


def test_operator_wait_regrant_cadence_is_decoupled_from_idle_backoff_cap(
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
            reason="venue submission needs operator authorization",
            waiting=True,
            waiting_reason="venue submission needs operator authorization",
            waiting_contract=WaitingContract(
                blocker_fingerprint=(
                    "submission_gate_closed_no_external_submission_authorized_8d2b840c"
                ),
                recheck_condition="operator authorizes venue submission",
                recheck_token="token-v1",
                wait_mode="event",
                wake_on=("authorization",),
                operator_action_required=True,
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    # The regrant check reads the module-level name in _planning_context (a
    # from-import), so the consuming module is what must be patched.
    monkeypatch.setattr(
        "argus_skill.life.supervisor._planning_context."
        "OPERATOR_WAIT_TURN_REGRANT_SECONDS",
        4 * IDLE_BACKOFF_CAP_SECONDS,
    )
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    # Aged just past the idle backoff cap but short of the regrant cadence:
    # if the two were still coupled this would grant a turn.
    wait_path = next(life.glob("planner-waiting-contract-*.json"))
    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    wait_state["idle_capacity_turn_ts"] = time.time() - IDLE_BACKOFF_CAP_SECONDS - 1
    supervisor._write_planner_waiting_contract_state(wait_state)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    wait_state["idle_capacity_turn_ts"] = (
        time.time() - 4 * IDLE_BACKOFF_CAP_SECONDS - 1
    )
    supervisor._write_planner_waiting_contract_state(wait_state)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 3


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
                wait_mode="event",
                wake_on=("authorization",),
                operator_action_required=False,
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 1
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == 2

    # Without operator_action_required the timed regrant path never applies:
    # a turn timestamp aged far past the cadence still grants nothing.
    wait_path = next(life.glob("planner-waiting-contract-*.json"))
    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    wait_state["idle_capacity_turn_ts"] = (
        time.time() - 100 * OPERATOR_WAIT_TURN_REGRANT_SECONDS
    )
    supervisor._write_planner_waiting_contract_state(wait_state)
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
        wait_mode="event",
        wake_on=("subagent_state",),
        observed_revision="live",
    )

    assert supervisor._persist_planner_waiting_contract(contract) is None
    assert list((tmp_path / "life").glob("planner-waiting-contract-*.json")) == []


def test_subagent_event_wait_without_host_revision_degrades_to_poll(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="live-subagents:abc",
        recheck_condition="data-build changes state",
        recheck_token="run-1",
        wait_mode="event",
        wake_on=("subagent_state",),
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "poll"
    assert state["wake_on"] == []
    assert state["recheck_after_seconds"] == 300


def test_event_wait_without_wake_source_degrades_to_poll(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="external:abc",
        recheck_condition="external state changes",
        recheck_token="run-1",
        wait_mode="event",
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "poll"
    assert state["recheck_after_seconds"] == 300


def test_event_wait_with_unknown_wake_source_degrades_without_planner_error(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="external:abc",
        recheck_condition="external state changes",
        recheck_token="run-1",
        wait_mode="event",
        wake_on=("unknown_source",),
    )

    events: list[dict] = []
    supervisor._emit = lambda event: events.append(event) or True

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "poll"
    assert state["wake_on"] == []
    assert state["recheck_after_seconds"] == 300
    assert not [event for event in events if event["type"] == "life.planner.error"]
    normalized = next(
        event
        for event in events
        if event["type"] == "life.planner.waiting_contract.normalized"
    )
    assert normalized["degraded"] is True


def test_artifact_event_wait_without_paths_degrades_to_poll(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="external:abc",
        recheck_condition="artifact changes",
        recheck_token="run-1",
        wait_mode="event",
        wake_on=("artifact_revision",),
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "poll"
    assert state["wake_on"] == []


def test_compound_synonym_wake_sources_persist_as_durable_event_wait(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    events: list[dict] = []
    supervisor._emit = lambda event: events.append(event) or True
    contract = WaitingContract(
        blocker_fingerprint="operator-or-artifact:abc",
        recheck_condition="operator answers or the artifact changes",
        recheck_token="run-1",
        wait_mode="EVENT",
        wake_on=("operator_answer|artifact_revision",),
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "event"
    assert state["wake_on"] == ["authorization"]
    assert not [event for event in events if event["type"] == "life.planner.error"]
    assert any(
        event["type"] == "life.planner.waiting_contract.normalized"
        for event in events
    )


def test_watched_paths_derive_artifact_revision_and_unknown_mode(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="artifact:abc",
        recheck_condition="the result artifact changes",
        recheck_token="run-1",
        wait_mode="on_change",
        watched_paths=("results/output.json",),
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "event"
    assert state["wake_on"] == ["artifact_revision"]
    assert state["watched_paths"] == ["results/output.json"]


def test_resolvable_wait_id_derives_host_observed_subagent_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_direct_job(project)
    supervisor = _supervisor(project, tmp_path / "life")
    contract = WaitingContract(
        blocker_fingerprint="subagent:data-build",
        recheck_condition="data-build changes state",
        recheck_token="run-1",
        wait_mode="poll",
        wait_id="data-build",
    )

    state = supervisor._persist_planner_waiting_contract(contract)

    assert state is not None
    assert state["wait_mode"] == "event"
    assert state["wake_on"] == ["subagent_state"]
    assert state["source_wait_id"] == "data-build"
    assert state["observed_revision"]


def test_wake_normalization_does_not_relax_watched_path_confinement(
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
        wait_mode="event",
        wake_on=("artifact_change",),
        watched_paths=("../outside.json",),
    )

    assert supervisor._persist_planner_waiting_contract(contract) is None
    assert any(
        event.get("error") == "planner wait has unsafe watched path"
        for event in events
    )
    assert list((tmp_path / "life").glob("planner-waiting-contract-*.json")) == []
