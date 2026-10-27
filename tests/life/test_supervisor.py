from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.event_catalog import EventType
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    global_daily_spend,
)
from argus.life.supervisor._constants import PLANNER_DEDUP_STATUSES


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
            from argus.life.event_log import JsonlEventSink

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
    stop_kind: str | None = None
    park_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "done"
    operator_question: str = ""
    operator_options: list[dict[str, Any]] = field(default_factory=list)
    research_result: dict[str, Any] | None = None


class _ResearchParkRunner:
    def execute(self, **kwargs) -> _Outcome:
        return _Outcome(
            success=False,
            status="parked",
            park_reason="research_open",
            stop_reason="doctoral target not reached",
        )


class _MaintenanceRunner:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.success = True
        self.status = "done"
        self.review_status = "done"

    def execute(self, **kwargs) -> _Outcome:
        self.kwargs = kwargs
        if kwargs.get("maintenance_mission"):
            Path(kwargs["working_dir_override"], "reviewed-change.txt").write_text(
                "reviewed\n",
                encoding="utf-8",
            )
        outcome = _Outcome(success=self.success, status=self.status)
        outcome.final_review_status = self.review_status
        outcome.final_review_source = "reviewer"
        return outcome


def test_an_answered_task_starts_and_ends_under_the_operator_s_request(tmp_path) -> None:
    """Trial, 2026-10-21: the mission view's top read the roles' English
    decision brief once an answered task ran."""
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(BacklogItem.new(
        title="pick A", objective="Authoritative answer from the operator:\nA",
        tags=["scope:bounded"],
        manager_decision={
            "routed": True, "vertical": "software", "workflow_mode": "direct",
            "display_objective": "选 A 还是 B？",
        },
    ))
    sink = _RecordingSink(memory.root)
    supervisor = LifeSupervisor(
        memory=memory, runner=_MaintenanceRunner(), sink=sink,
        config=LifeSupervisorConfig(project_worktree=tmp_path, artifact_root=tmp_path),
    )
    supervisor._vertical_resolved = True
    supervisor.tick()

    ends = (EventType.LIFE_MISSION_STARTED, EventType.LIFE_MISSION_COMPLETED)
    assert {event["type"]: event["objective"] for event in sink.events if event["type"] in ends} == {
        EventType.LIFE_MISSION_STARTED: "选 A 还是 B？", EventType.LIFE_MISSION_COMPLETED: "选 A 还是 B？",
    }


def test_claim_lost_is_not_counted_as_mission(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    first = memory.backlog.add(BacklogItem.new(
        title="stale",
        objective="already completed by another supervisor",
        priority=1,
    ))
    second = memory.backlog.add(BacklogItem.new(
        title="still ready",
        objective="run after the coordination miss",
        priority=2,
    ))
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MaintenanceRunner(),
        sink=_RecordingSink(memory.root),
        config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    original_next_pending = memory.backlog.next_pending
    raced = False

    def stale_next_pending(*args: Any, **kwargs: Any) -> BacklogItem | None:
        nonlocal raced
        item = original_next_pending(*args, **kwargs)
        if not raced and item is not None and item.id == first.id:
            raced = True
            assert memory.backlog.update(first.id, status="done") is not None
        return item

    monkeypatch.setattr(memory.backlog, "next_pending", stale_next_pending)

    result = supervisor.run()

    assert result["missions_started"] == 0
    assert result["missions_run"] == 0
    assert result["results"] == []
    rows = {item.id: item for item in memory.backlog.all()}
    assert rows[first.id].status == "done"
    assert rows[second.id].status == "pending"


def test_framework_maintenance_uses_isolated_worktree_and_review(
    tmp_path,
    monkeypatch,
) -> None:
    import subprocess

    source = tmp_path / "framework"
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "checkout", "-qb", "main"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=source, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=source,
        check=True,
    )
    (source / "baseline.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "baseline.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=source, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)], cwd=source, check=True
    )
    subprocess.run(
        ["git", "push", "-qu", "origin", "main"], cwd=source, check=True
    )
    monkeypatch.setattr(
        "argus.core.runtime_identity.source_root",
        lambda: source,
    )

    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "project"
    project.mkdir()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=project,
        ),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="repair framework",
        objective="fix observed defect",
        tags=["framework_maintenance", "review:required", "scope:bounded"],
        execution_workdir="",
        context_refs=[{
            "ref": str(memory.root / "events.jsonl"),
            "why": "life.planner.error showed the observed harness failure",
        }],
        acceptance_check=(
            "python -c \"from pathlib import Path; "
            "raise SystemExit(not Path('reviewed-change.txt').is_file())\""
        ),
        manager_decision={
            "routed": True,
            "vertical": "argus_maintenance",
            "workflow_mode": "direct",
        },
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "parked"
    assert result["review_status"] == "done"
    worktree = Path(runner.kwargs["working_dir_override"])
    assert worktree.is_dir()
    assert worktree.is_relative_to(memory.root / "maintenance" / "worktrees")
    assert worktree != source
    assert runner.kwargs["maintenance_mission"] is True
    assert runner.kwargs["vertical_override"] == "argus_maintenance"
    assert runner.kwargs["require_independent_review"] is True
    assert runner.kwargs["allow_skill_changes"] is False
    settled = next(row for row in memory.backlog.history() if row.id == item.id)
    assert settled.status == "parked"
    assert settled.execution_workdir == str(worktree)
    assert [
        option["id"] for option in settled.operator_decision["options"]
    ] == ["adopt", "decline"]
    assert settled.operator_decision["decision_kind"] == "framework_deployment"
    assert settled.pending_question == (
        "The Reviewer read the change for “repair framework” and it holds. "
        "Publish this reviewed version and use it at the next task boundary?"
    )
    sidecar = json.loads(
        (memory.root / "maintenance" / "pending" / f"{item.id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["worktree"] == str(worktree)
    assert sidecar["reviewed_candidate"] != sidecar["public_base"]
    assert settled.operator_decision['reviewed_candidate'] == sidecar['reviewed_candidate']
    assert 'input_digest' not in sidecar and 'approval_binding' not in sidecar
    assert 'private_remote' not in sidecar and 'acceptance_command' not in sidecar

    runner.success = False
    runner.status = "error"
    runner.review_status = "continue"
    rejected = memory.backlog.add(BacklogItem.new(
        title="rejected framework repair",
        objective="leave a rejected change undeployed",
        tags=["framework_maintenance", "review:required", "scope:bounded"],
        execution_workdir="",
        context_refs=[{
            "ref": str(memory.root / "events.jsonl"),
            "why": "observed failure",
        }],
        acceptance_check="python -c \"raise SystemExit(1)\"",
        manager_decision={"routed": True, "vertical": "argus_maintenance"},
    ))

    rejected_result = supervisor.tick()

    assert rejected_result is not None
    assert rejected_result["status"] == "error"
    rejected_row = next(
        row for row in memory.backlog.history() if row.id == rejected.id
    )
    assert rejected_row.operator_decision == {}
    rejected_worktree = Path(runner.kwargs["working_dir_override"])
    assert rejected_row.execution_workdir == str(rejected_worktree)
    assert (memory.root / "maintenance" / "pending" / f"{rejected.id}.json").is_file()
    assert (rejected_worktree / "reviewed-change.txt").is_file()


@pytest.mark.parametrize("evidence_path", ["", "tracked.txt", "untracked.txt", "evidence.log"])
def test_maintenance_creation_failure_preserves_checkout_hook_evidence(
    tmp_path, monkeypatch, evidence_path,
) -> None:
    import subprocess

    source = tmp_path / "framework"
    source.mkdir()
    origin = tmp_path / "origin.git"

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source, check=True, capture_output=True, text=True,
        )

    git("init", "--bare", "-q", str(origin))
    git("init", "-q")
    git("checkout", "-qb", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (source / "tracked.txt").write_text("reviewed content\n")
    (source / ".gitignore").write_text("evidence.log\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    git("remote", "add", "origin", str(origin))
    git("push", "-qu", "origin", "main")
    if evidence_path:
        hook = source / ".git" / "hooks" / "post-checkout"
        hook.write_text(
            "#!/bin/sh\n" + f"printf 'checkout hook evidence\\n' > {evidence_path}\n",
        )
        hook.chmod(0o755)
    monkeypatch.setattr("argus.core.runtime_identity.source_root", lambda: source)
    memory = LifeMemory.open(tmp_path / "life")
    project = tmp_path / "project"
    project.mkdir()
    supervisor = LifeSupervisor(
        memory=memory, runner=_MaintenanceRunner(), sink=_RecordingSink(memory.root),
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="framework maintenance", objective="repair", tags=["framework_maintenance"],
    ))
    worktree = memory.root / "maintenance" / "worktrees" / item.id
    sidecar = memory.root / "maintenance" / "pending" / f"{item.id}.json"
    write_text = Path.write_text

    def fail_sidecar(path, *args, **kwargs):
        if path == sidecar:
            raise OSError("simulated sidecar write failure")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_sidecar)

    with pytest.raises(OSError, match="simulated sidecar write failure"):
        supervisor._resolve_mission_workdir(item)

    assert not sidecar.exists()
    if evidence_path:
        assert (worktree / evidence_path).read_text() == "checkout hook evidence\n"
        # Git's porcelain paths use forward slashes on Windows as well.
        assert f"worktree {worktree.as_posix()}\n" in git("worktree", "list", "--porcelain").stdout
    else:
        assert not worktree.exists()


def test_stale_item_vertical_falls_back_without_unknown_vertical_crash(
    tmp_path,
) -> None:
    from argus.skills.vertical_select import persist_vertical

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
    from argus.manager.directive import set_active_manager_directive
    from argus.skills.vertical_select import persist_vertical

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
        "optimize the current project\n\n## OperatorContext"
    )
    assert "Build the Apple-specific inference kernel." in calls[0]
    assert first["vertical"] == second["vertical"] == "device_tuning"


def test_research_park_is_paused_and_resumable(tmp_path) -> None:
    assert "parked" in PLANNER_DEDUP_STATUSES
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchParkRunner(),
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
    assert result["status"] == "parked"
    paused = next(
        (candidate for candidate in mem.backlog.all() if candidate.id == item.id),
        None,
    )
    assert paused is not None
    assert paused.status == "parked" and paused.outcome["park"] == {"reason": "research_open"}
    assert paused.last_error == "doctoral target not reached"
    event = next(
        event
        for event in sink.events
        if event.get("type") == "life.mission.completed"
    )
    assert event["success"] is False
    assert event["resumable"] is True
    assert (event["park_reason"], event["outcome_class"]) == ("research_open", "incomplete")
    (experience,) = mem.failure_experiences.recent()
    assert experience.mission_id == item.id
    assert experience.status == "parked"
    assert "not a general impossibility" in experience.claim_boundaries[0]

    [resumed] = mem.backlog.resume_parked()
    assert resumed is not None
    assert resumed.status == "pending"
    assert resumed.attempt == 2


def _append_usage(project, call_id: str, completed_at: float, cost_usd: float) -> None:
    from argus.core.usage import UsageLedger, UsageRecord

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


def test_global_daily_spend_observes_new_cost_without_ttl_staleness(tmp_path) -> None:
    now = time.time()
    root = tmp_path / "root"
    project = root / "projects" / "p1"
    _append_usage(project, "first-call", now, 1.0)
    assert global_daily_spend(global_root=root, now=now) == pytest.approx(1.0)

    _append_usage(project, "new-call", now + 1, 2.0)

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.0)


class _BlockedQuestionRunner:
    """A runner whose mission stops with a reviewer 'blocked' verdict carrying
    an operator_question — the shape apps/_runtime.py's real execute()
    produces (``_Outcome.operator_question``, extracted from the final
    round's ReviewDecision when the round settles on the operator)."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(
            success=False, status="operator", final_message="needs a decision",
            operator_question="fp16 精度损失可以接受吗，还是必须 fp32？",
            operator_options=[
                {
                    "id": "allow-fp16",
                    "label": "允许 fp16",
                    "description": "接受精度损失并继续优化。",
                },
                {
                    "id": "require-fp32",
                    "label": "必须 fp32",
                    "description": "保持 fp32 精度约束。",
                },
            ],
        )


class _LateForbidQuestionRunner(_BlockedQuestionRunner):
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root

    def execute(self, **kwargs: Any) -> _Outcome:
        from argus.core.knob_store import write_persisted_knob

        write_persisted_knob("ARGUS_SKILL_OPERATOR_AVAILABLE", "0")
        return super().execute(**kwargs)


def test_late_forbid_still_parks_the_question_for_the_manager(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    sup = LifeSupervisor(
        memory=mem,
        runner=_LateForbidQuestionRunner(mem.root),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Optimize matmul kernel",
        objective="make it 2x faster",
    ))

    result = sup.tick()

    assert result is not None
    # The role named the operator. With nobody to ask, the card parks and the
    # Manager decides it in the operator's place; the host never reroutes it.
    assert result["status"] == "operator"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "parked"
    assert stored.pending_question


class _TechnicalQuestionRunner:
    """A recoverable benchmark choice incorrectly phrased as a human question."""

    def execute(self, **kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            success=False,
            status="operator",
            stop_reason="the largest benchmark row timed out",
            operator_question="Should the benchmark use a smaller diagnostic shape?",
        )
        outcome.final_review_reason = "The largest benchmark row timed out."
        outcome.final_review_next_action = (
            "Validate one smaller row, then replan the full measurement."
        )
        return outcome


def test_no_operator_parks_the_question_for_the_manager(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_TechnicalQuestionRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Measure the kernel", objective="compare baseline and candidate",
    ))

    result = sup.tick()

    assert result is not None and result["status"] == "operator"
    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert stored.status == "parked"
    assert stored.pending_question == (
        "Should the benchmark use a smaller diagnostic shape?"
    )


class _HandedToManagerRunner:
    """A ``→ manager`` from ``source``, with no question, in its own words."""

    def __init__(self, source: str = "engineer_handoff") -> None:
        self.source = source

    def execute(self, **kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            success=False, status="manager", final_message="A and B conflict.",
            stop_reason="A and B conflict; the choice is not mine.",
        )
        outcome.final_review_source = self.source
        return outcome


@pytest.mark.parametrize("source", ["engineer_handoff", "reviewer"])
def test_work_handed_to_the_manager_parks_for_it_instead_of_failing(tmp_path, source) -> None:
    """2026-10-20: a task an Engineer handed to the Manager settled as failed a
    second after its round, before the Manager could answer; its question then
    had nothing to park. It now parks where a Manager stop leaves work. A
    Reviewer's ``→ manager`` (HO-3) parks the same way, not queued back to the
    Engineer."""
    from argus.life.memory import park_reason

    mem = LifeMemory.open(tmp_path / "life")
    sup = LifeSupervisor(
        memory=mem,
        runner=_HandedToManagerRunner(source),
        sink=_RecordingSink(mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(title="Pick A or B", objective="change truncate"))

    sup.tick()

    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert park_reason(stored) == "research_open"
    assert stored.pending_question == ""
    assert "the choice is not mine" in stored.last_error


class _ClimbedToManagerRunner:
    """Work that climbed to the Manager on a record no role wrote (the host's)."""

    def execute(self, **kwargs: Any) -> _Outcome:
        outcome = _Outcome(
            success=False, status="manager", final_message="Nothing changed.",
            stop_reason="Nothing changed since the planner last held this work.",
        )
        outcome.final_review_source = "reviewer_unavailable"
        return outcome


def test_a_task_handed_to_the_manager_waits_whoever_handed_it(tmp_path) -> None:
    """Live 2026-10-26: a research task that climbed to the Manager read as
    failed. Handed to the Manager by any record, a task is parked in its hands,
    and its completion carries no failure."""
    from argus.life.memory import park_reason

    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem, runner=_ClimbedToManagerRunner(), sink=sink,
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    item = mem.backlog.add(BacklogItem.new(title="Survey the field", objective="map prior work"))

    sup.tick()

    stored = next(row for row in mem.backlog.all() if row.id == item.id)
    assert (stored.status, park_reason(stored)) == ("parked", "research_open")
    assert stored.outcome["execution_status"] == "paused"
    completed = next(e for e in sink.events if e.get("type") == EventType.LIFE_MISSION_COMPLETED)
    assert completed["status"] == "parked"
    assert (completed["failure_reason"], completed["terminal_status"]) == ("", "")
    assert completed["outcome_class"] != "failed"


def test_the_wait_for_an_answer_is_told_in_the_operator_s_language(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    item = mem.backlog.add(BacklogItem.new(title="选数据集", objective="跑基线"))
    mem.backlog.update(item.id, park_reason="operator_pause", pending_question="基线用哪个数据集？")
    sink = _RecordingSink(mem.root)
    LifeSupervisor(
        memory=mem,
        runner=_BlockedQuestionRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2), poll_interval_seconds=0.01,
            continuous=True, continuous_objective="完成基准测试",
        ),
    ).run()

    statuses = [event.get("text", "") for event in sink.events if event.get("type") == "life.operator_status"]
    assert "在等你回答：基线用哪个数据集？" in statuses


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
            status="planner",
            stop_reason="reviewer refuted node; premise unsatisfiable",
        )
        if self._stage_transition is not None:
            outcome.stage_transition = self._stage_transition
        return outcome


class _QuestionRunner:
    """Hands the work to the operator with a question."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(
            success=False,
            status="operator",
            stop_reason="the approved trust boundary is ambiguous",
            operator_question="May this function be added to the trusted boundary?",
        )


def test_a_question_uses_the_durable_answer_path(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_QuestionRunner(), sink=sink, config=cfg,
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
    assert result["status"] == "operator"
    assert not [
        entry
        for entry in mem.journal.all()
        if entry.kind == "mission_replan_requested" and entry.id == item.id
    ]

    # The question survives a process restart, and the answer resumes the
    # task it parked.
    reopened = LifeMemory.open(tmp_path / "life")
    blocked = next(row for row in reopened.backlog.all() if row.id == item.id)
    assert blocked.status == "parked"
    assert blocked.pending_question == (
        "May this function be added to the trusted boundary?"
    )
    assert reopened.backlog.resume_parked() == []
    assert reopened.backlog.next_pending() is None
    waiting = next(row for row in reopened.backlog.all() if row.id == dependent.id)
    assert waiting.status == "pending"
    resumed, answered = reopened.backlog.resume_with_operator_reply(
        item.id,
        "No. Keep the trusted boundary unchanged.",
        manager_decision="Find a verified implementation without new assumptions.",
    )
    assert answered and resumed.status == "pending"
    assert resumed.pending_question == ""
    still = next(row for row in reopened.backlog.all() if row.id == dependent.id)
    assert still.deps == [item.id]


def test_operator_question_pauses_its_item_not_the_campaign(tmp_path) -> None:
    """An unanswered question used to stop the whole run. Overnight that cost
    run-06 about twenty hours and run-04 about twelve, each sitting on one
    question with a half-written paper and plenty of work that did not need the
    answer. The item stays the operator's; the campaign keeps going.
    """
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=2),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="verify the project",
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=_QuestionRunner(),
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

    assert result["results"][0]["status"] == "operator"
    assert result["stopped_by"] != "pending_operator_question"
    # The Planner is asked for work that does not depend on the answer.
    assert planned
    # And the question itself is still the operator's to answer.
    blocked = [
        item for item in mem.backlog.all()
        if str(getattr(item, "pending_question", "") or "").strip()
    ]
    assert blocked and blocked[0].status == "parked"


def test_replan_requested_always_returns_the_node_to_pending(tmp_path) -> None:
    """The Reviewer's ``replan_requested`` stands however often it repeats:
    a planned node goes back to pending for its plan's revision and is
    re-dispatched, never rewritten into a no-progress failure by a host-side
    count."""
    mem = LifeMemory.open(tmp_path / "life")
    runner = _CountingReplanRunner()
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=_RecordingSink(mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=10),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="unsatisfiable node", objective="drain an impossible obligation",
        plan_id="plan-1", plan_version=1,
    ))

    for calls in range(1, 5):
        result = sup.tick()
        assert result is not None and result["status"] == "planner"
        row = {row.id: row for row in mem.backlog.all()}[item.id]
        assert row.status == "pending"
        assert runner.calls == calls

    assert not [
        e for e in mem.journal.all()
        if e.kind == "mission_failed" and e.id == item.id
    ]


def test_stage_reconciled_replan_fails_with_the_stage_reason(tmp_path) -> None:
    """A ``replan_requested`` outcome that carries a Manager stage
    advance/rollback (``stage_reconciled_replan``) is marked failed with the
    stage-reconciliation reason."""
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    runner = _CountingReplanRunner(
        stage_transition={"action": "advance", "target_stage": "solve"},
    )
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(max_missions=10),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)
    item = mem.backlog.add(BacklogItem.new(
        title="stage advance node", objective="advance the pipeline stage",
    ))

    sup.tick()

    row = {r.id: r for r in mem.backlog.all()}[item.id]
    assert row.status == "failed"
    assert "manager advance" in (row.last_error or "")
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
