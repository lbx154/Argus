"""Integration test for the Manager stage-decision hook in the mission runner.

After each mission round, ``_SkillLoopRunner._decide_stage_transition`` hands the
final reviewer verdict to the Manager (the sole post-bootstrap writer of the
pipeline stage), which judges advance / hold / rollback and writes
``PIPELINE_STATE.json``. These tests drive that hook directly with a
``__new__``-built runner (no full ``__init__``) + a stub manager backend.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus_skill.apps import _runtime
from argus_skill.apps._runtime import _SkillLoopRunner
from argus_skill.core.models import ReviewDecision
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.skills.stage_checklists import resolve_stage_checklist_contract
from argus_skill.skills.vertical_select import persist_vertical


class _Result:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


class _StubRunner:
    def __init__(self, verdict: dict | str) -> None:
        self._text = verdict if isinstance(verdict, str) else json.dumps(verdict)

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        return _Result(self._text)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise RuntimeError("backend down")


class _NullMissionRunner:
    """Mission runner; never invoked when the backlog is empty."""


class _PlannerCalled(Exception):
    pass


class _ExplodingPlannerRunner:
    def run_exec(self, *, prompt: str, options, run_label: str, resume_thread_id=None):  # noqa: ANN001
        raise _PlannerCalled("planner should not run before Manager rollback")


class _MissionOutcome:
    success = True
    status = "done"
    stop_reason = ""
    rounds = 1
    matched_skill_name = ""
    skill_distilled = False
    had_follow_up = False
    final_submission_certified = False
    completion_evidence = ""
    planner_report: dict = {}
    checklist_feedback: dict = {}
    step_back = None
    auth_failure = False


class _StageMissionRunner:
    def __init__(self, action: str) -> None:
        self.action = action

    def execute(
        self,
        *,
        objective: str,
        sink,  # noqa: ANN001
        prelude_context: str = "",
        scope: str = "",
        original_objective: str = "",
    ) -> _MissionOutcome:
        outcome = _MissionOutcome()
        outcome.stage_transition = {
            "action": self.action,
            "current_stage": "scope",
            "target_stage": "solve" if self.action == "advance" else "scope",
        }
        return outcome


class _WritesRollbackPacketMissionRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.calls = 0

    def execute(
        self,
        *,
        objective: str,
        sink,  # noqa: ANN001
        prelude_context: str = "",
        scope: str = "",
        original_objective: str = "",
    ) -> _MissionOutcome:
        self.calls += 1
        _seed_manager_blocked_packet(self.project_root)
        return _MissionOutcome()


class _EmptyThenRunner:
    """Returns ``empties`` empty turns (the gpt-5.5/fnyweg flake) then the real
    verdict — exercises decide_stage_transition's empty-output retry."""

    def __init__(self, verdict: dict, *, empties: int = 1) -> None:
        self._text = json.dumps(verdict)
        self._empties_left = empties
        self.calls = 0

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        self.calls += 1
        if self._empties_left > 0:
            self._empties_left -= 1
            return _Result("")
        return _Result(self._text)


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


class _Round:
    def __init__(self, review) -> None:  # noqa: ANN001
        self.review = review


def _runner_with(backend) -> _SkillLoopRunner:  # noqa: ANN001
    r = _SkillLoopRunner.__new__(_SkillLoopRunner)
    r.manager_backend = backend
    r._backend = backend
    return r


def _review(
    status: str = "done",
    *,
    checklist: list[dict] | None = None,
    forward_progress: bool | None = True,
    scope: str = "",
) -> ReviewDecision:
    report = {"headline": "done"}
    if forward_progress is not None:
        report["forward_progress"] = forward_progress
    return ReviewDecision(
        status=status,  # type: ignore[arg-type]
        reason="checklist satisfied",
        next_action="advance",
        checklist=(
            checklist
            if checklist is not None
            else [
                {
                    "item": "research.first_score_plan",
                    "satisfied": True,
                    "evidence": "X",
                }
            ]
        ),
        scope=scope,
        planner_report=report,
    )


def _project(tmp_path: Path, *, current: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": current}), encoding="utf-8"
    )
    return tmp_path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _submission_project(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "submission",
            "stages": {
                "research": {"status": "done"},
                "plan": {"status": "done"},
                "benchmark": {"status": "done"},
                "run": {"status": "done"},
                "analysis": {"status": "done"},
                "draft": {"status": "done"},
                "review": {"status": "done"},
                "submission": {"status": "pending"},
            },
        },
    )
    return tmp_path


def _seed_manager_blocked_packet(root: Path) -> None:
    evidence_files = {
        "analysis_route_decision": "paper/ANALYSIS_ROUTE_DECISION.json",
        "evidence_bundle": "experiments/run_stage/EVIDENCE_BUNDLE.json",
        "manager_action_request": "research/MANAGER_ACTION_REQUEST.json",
        "pipeline_state": "research/PIPELINE_STATE.json",
        "run_stage_routing_request": "experiments/run_stage/RUN_STAGE_ROUTING_REQUEST.json",
    }
    for key, rel in evidence_files.items():
        if key != "pipeline_state":
            _write_json(root / rel, {"ok": True})
    _write_json(
        root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json",
        {
            "outcome": "MANAGER_BLOCKED",
            "status": "rollback-accepted",
            "requested_stage": "submission",
            "current_stage": "submission",
            "earliest_broken_stage": "run",
            "rollback_target": "run",
            "manager_action_required": "rollback_stage_to_run",
            "pipeline_stage_fields_clean": True,
            "evidence_files": evidence_files,
        },
    )


def _stage(root: Path) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["current_stage"]


def test_replan_control_outcome_does_not_run_manager_stage_transition() -> None:
    assert _runtime._should_run_stage_transition("replan_requested") is False
    assert _runtime._should_run_stage_transition("paused_budget") is False
    assert _runtime._should_run_stage_transition("done") is True


def test_open_ended_terminal_planner_error_triggers_manager_rollback(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "review",
            "vertical": "math",
            "stages": {
                "scope": {"status": "done"},
                "solve": {"status": "done"},
                "review": {"status": "done"},
            },
        },
    )
    backend = _StubRunner({
        "action": "rollback",
        "target_stage": "solve",
        "reason": "the open problem remains unresolved",
    })
    sink = _Sink()
    statuses: list[str] = []
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.config = SimpleNamespace(
        open_ended=True,
        continuous_objective="Continue until proof or counterexample.",
        artifact_root=tmp_path,
    )
    supervisor.planner_runner = backend
    supervisor.skill_store = None
    supervisor.sink = sink
    supervisor._emit = sink.handle_event  # type: ignore[method-assign]
    supervisor._emit_status = statuses.append  # type: ignore[method-assign]
    supervisor._reset_idle_backoff = lambda: None  # type: ignore[method-assign]
    supervisor._last_open_ended_project_done_signature = "old"
    verdict = SimpleNamespace(
        project_done=False,
        new_tasks=[],
        reason="review checkpoint done, objective unresolved",
    )

    assert supervisor._reconcile_open_ended_terminal_stage(verdict) is True
    assert _stage(tmp_path) == "solve"
    assert supervisor._last_open_ended_project_done_signature == ""
    assert any("reopened open-ended campaign" in text for text in statuses)
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "rollback"
        for event in sink.events
    )


def test_hook_advances_stage_and_emits_event(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    assert decision["action"] == "advance"
    assert decision["diagnostic"] == "valid_target"
    assert _stage(root) == "plan"
    assert any(e.get("type") == "life.manager.stage_decision" for e in sink.events)
    # The retired self-reported confidence must not leak into the event payload.
    assert "confidence" not in decision


def test_bounded_item_stays_pending_after_intermediate_stage_advance(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(
        BacklogItem.new(title="full bounded task", objective="finish every stage")
    )
    sink = _Sink()
    sup = LifeSupervisor(
        memory=memory,
        runner=_StageMissionRunner("advance"),
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    result = sup.tick()

    assert result is not None
    assert result["status"] == "stage_continues"
    persisted = next(entry for entry in memory.backlog.all() if entry.id == item.id)
    assert persisted.status == "pending"
    assert not any(
        event.get("type") == "life.mission.completed" for event in sink.events
    )


def test_bounded_item_finishes_only_after_final_stage_complete(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(
        BacklogItem.new(title="full bounded task", objective="finish every stage")
    )
    sup = LifeSupervisor(
        memory=memory,
        runner=_StageMissionRunner("complete"),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    result = sup.tick()

    assert result is not None
    assert result["status"] == "done"
    persisted = next(entry for entry in memory.backlog.all() if entry.id == item.id)
    assert persisted.status == "done"


def test_bounded_stage_hold_stays_pending_without_immediate_rerun(tmp_path: Path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(
        BacklogItem.new(title="full bounded task", objective="finish every stage")
    )
    sup = LifeSupervisor(
        memory=memory,
        runner=_StageMissionRunner("hold"),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    result = sup.run()

    assert result["stopped_by"] == "stage_hold"
    assert result["missions_run"] == 1
    persisted = next(entry for entry in memory.backlog.all() if entry.id == item.id)
    assert persisted.status == "pending"


def test_hook_retries_on_empty_output_then_advances(tmp_path: Path, monkeypatch) -> None:
    # An empty manager turn (gpt-5.5/fnyweg flake) must NOT silently default-HOLD
    # and wedge the stage — it retries and picks up the real advance verdict.
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _EmptyThenRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}, empties=1
    )
    runner = _runner_with(backend)
    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=_Sink()
    )
    assert backend.calls == 2  # one empty, then retried into the real verdict
    assert decision["action"] == "advance"
    assert _stage(root) == "plan"


def test_hook_persistent_empty_done_satisfied_advances(
    tmp_path: Path, monkeypatch
) -> None:
    # If every Manager turn is empty after a certified reviewer verdict, the
    # Manager-owned fallback advances to the immediate next stage.
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _EmptyThenRunner({}, empties=99)
    runner = _runner_with(backend)
    sink = _Sink()
    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )
    assert backend.calls == 3
    assert decision["action"] == "advance"
    assert decision["target_stage"] == "plan"
    assert decision["diagnostic"] == "empty_output_certified_advance"
    assert _stage(root) == "plan"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["diagnostic"] == "empty_output_certified_advance"


def test_hook_persistent_empty_done_satisfied_completes_final_stage(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _submission_project(tmp_path)
    backend = _EmptyThenRunner({}, empties=99)
    runner = _runner_with(backend)
    sink = _Sink()
    checklist = resolve_stage_checklist_contract(
        "submission",
        project_root=root,
    )
    review = _review(checklist=[
        {
            "item": item.id,
            "satisfied": True,
            "evidence": f"verified {item.evidence_hint}",
        }
        for item in checklist.items
    ])

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(review)], workdir=root, sink=sink
    )

    assert backend.calls == 3
    assert decision["action"] == "complete"
    assert decision["target_stage"] == "submission"
    assert decision["diagnostic"] == "empty_output_no_next_stage"
    state = json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["stages"]["submission"]["status"] == "done"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["action"] == "complete"
    assert event["diagnostic"] == "empty_output_no_next_stage"


def test_hook_does_not_complete_bounded_final_stage_without_required_checklist(
    tmp_path: Path,
) -> None:
    root = _submission_project(tmp_path)
    backend = _StubRunner({"action": "hold", "reason": "no next stage"})
    runner = _runner_with(backend)

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review(checklist=[]))],
        workdir=root,
        sink=_Sink(),
        mission_scope="bounded",
    )

    assert decision["action"] == "hold"
    assert decision["target_stage"] == "submission"


def test_hook_persistent_empty_unsatisfied_checklist_holds(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _EmptyThenRunner({}, empties=99)
    runner = _runner_with(backend)
    sink = _Sink()
    review = _review(
        checklist=[
            {"item": "research.first_score_plan", "satisfied": False, "evidence": ""}
        ]
    )
    decision = runner._decide_stage_transition(
        rounds_list=[_Round(review)], workdir=root, sink=sink
    )
    assert decision["action"] == "hold"
    assert decision["diagnostic"] == "empty_output_unsatisfied_checklist"
    assert _stage(root) == "research"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["diagnostic"] == "empty_output_unsatisfied_checklist"


def test_hook_no_review_holds_and_does_not_write(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "x"}
    ))
    sink = _Sink()

    # empty rounds_list → no final review → Manager HOLDs, writes nothing.
    decision = runner._decide_stage_transition(rounds_list=[], workdir=root, sink=sink)

    assert decision["action"] == "hold"
    assert decision["source"] == "no_review_hold"
    assert decision["diagnostic"] == ""
    assert _stage(root) == "research"


def test_hook_parse_hold_event_carries_diagnostic(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner("not json at all"))
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    assert decision["action"] == "hold"
    assert decision["source"] == "manager_llm"
    assert decision["diagnostic"] == "no_json_object"
    assert _stage(root) == "research"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["diagnostic"] == "no_json_object"


def test_hook_backend_error_holds_and_never_raises(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_BoomRunner())
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    # Manager swallows the LLM error → fail-safe HOLD; stage untouched.
    assert decision["action"] == "hold"
    assert _stage(root) == "research"


def test_hook_consumes_manager_blocked_rollback_artifact_before_no_review_hold(
    tmp_path: Path,
) -> None:
    root = _submission_project(tmp_path)
    _seed_manager_blocked_packet(root)
    runner = _runner_with(_BoomRunner())
    sink = _Sink()

    decision = runner._decide_stage_transition(rounds_list=[], workdir=root, sink=sink)

    assert decision["action"] == "rollback"
    assert decision["target_stage"] == "run"
    assert decision["source"] == "manager_blocked_rollback_artifact"
    assert decision["diagnostic"] == "accepted_manager_blocked_artifact"
    assert _stage(root) == "run"

    state = json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["stages"]["run"]["status"] == "done"
    assert state["stages"]["analysis"]["status"] == "pending"
    assert state["stages"]["draft"]["status"] == "pending"
    assert state["stages"]["review"]["status"] == "pending"
    assert state["stages"]["submission"]["status"] == "pending"
    assert state["rollback_history"][-1]["from_stage"] == "submission"
    assert state["rollback_history"][-1]["to_stage"] == "run"
    assert state["stage_history"][-1]["direction"] == "rollback"
    assert state["stage_history"][-1]["by"] == "manager"

    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["action"] == "rollback"
    assert event["source"] == "manager_blocked_rollback_artifact"
    assert event["diagnostic"] == "accepted_manager_blocked_artifact"


def test_hook_consumes_manager_blocked_rollback_artifact_before_review_llm(
    tmp_path: Path,
) -> None:
    root = _submission_project(tmp_path)
    _seed_manager_blocked_packet(root)
    runner = _runner_with(_BoomRunner())
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review(status="continue", forward_progress=False))],
        workdir=root,
        sink=sink,
    )

    assert decision["action"] == "rollback"
    assert decision["target_stage"] == "run"
    assert decision["source"] == "manager_blocked_rollback_artifact"
    assert decision["diagnostic"] == "accepted_manager_blocked_artifact"
    assert _stage(root) == "run"

    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["action"] == "rollback"
    assert event["source"] == "manager_blocked_rollback_artifact"
    assert event["diagnostic"] == "accepted_manager_blocked_artifact"


def test_supervisor_consumes_manager_blocked_rollback_before_planner(
    tmp_path: Path,
) -> None:
    root = _submission_project(tmp_path / "project")
    _seed_manager_blocked_packet(root)
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    sup = LifeSupervisor(
        memory=memory,
        runner=_NullMissionRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="paper objective",
            project_worktree=root,
            artifact_root=root,
            paper_mission=False,
            full_paper_gate=False,
            open_ended=False,
        ),
        planner_runner=_ExplodingPlannerRunner(),
    )

    result = sup.run()

    assert result["stopped_by"] == "manager_blocked_rollback"
    assert _stage(root) == "run"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["action"] == "rollback"
    assert event["source"] == "manager_blocked_rollback_artifact"
    assert event["diagnostic"] == "accepted_manager_blocked_artifact"
    assert not any(e.get("type") == "life.planner.start" for e in sink.events)


def test_supervisor_consumes_manager_blocked_rollback_after_mission_before_hook(
    tmp_path: Path,
) -> None:
    root = _submission_project(tmp_path / "project")
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(
        BacklogItem.new(
            title="completed submission control mission",
            objective="refresh rollback-accepted packet",
        )
    )
    sink = _Sink()
    runner = _WritesRollbackPacketMissionRunner(root)
    post_mission_calls = {"count": 0}

    def _post_mission_hook(_outcome: dict) -> str:
        post_mission_calls["count"] += 1
        return "post_mission_hook_called"

    sup = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="paper objective",
            project_worktree=root,
            artifact_root=root,
            paper_mission=False,
            full_paper_gate=False,
            open_ended=False,
            post_mission_hook=_post_mission_hook,
        ),
        planner_runner=_ExplodingPlannerRunner(),
    )

    result = sup.run()

    assert runner.calls == 1
    assert result["stopped_by"] == "manager_blocked_rollback"
    assert post_mission_calls["count"] == 0
    assert _stage(root) == "run"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["action"] == "rollback"
    assert event["source"] == "manager_blocked_rollback_artifact"
    assert event["diagnostic"] == "accepted_manager_blocked_artifact"
    assert not any(e.get("type") == "life.post_mission.stop" for e in sink.events)
    assert not any(e.get("type") == "life.planner.start" for e in sink.events)


def test_supervisor_ignores_stale_manager_blocked_packet_after_one_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _submission_project(tmp_path / "project")
    _seed_manager_blocked_packet(root)
    memory = LifeMemory.open(tmp_path / "life")
    sup = LifeSupervisor(
        memory=memory,
        runner=_NullMissionRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="paper objective",
            project_worktree=root,
            artifact_root=root,
            paper_mission=False,
            full_paper_gate=False,
            open_ended=False,
        ),
        planner_runner=None,
    )
    assert sup.run()["stopped_by"] == "manager_blocked_rollback"
    assert _stage(root) == "run"

    planner_calls = {"count": 0}

    def _fake_plan_next_work():
        planner_calls["count"] += 1
        return "planner_retry"

    monkeypatch.setattr(sup, "_plan_next_work", _fake_plan_next_work)

    assert sup.run()["stopped_by"] == "planner_retry"
    assert planner_calls["count"] == 1
    assert _stage(root) == "run"
