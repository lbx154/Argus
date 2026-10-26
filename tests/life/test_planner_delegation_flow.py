"""Planner must delegate implementation and keep standing campaigns moving."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.daemon.state import write_continuous_config
from argus.life.event_log import JsonlEventSink
from argus.life.memory import (
    BacklogItem,
    LifeMemory,
)
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.planner import Planner, PlannerConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import plan, submit


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        submit(kwargs["options"], self.replies.pop(0))
        return RunnerResult(exit_code=0)


def _supervisor(project: Path, life: Path, planner: _PlannerBackend) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="keep optimizing Argus",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    # Isolate this flow test from host continuous.json state. The Planner class
    # still receives and forwards this provider in production.
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        open_ended=True,
    )
    return supervisor


def test_planner_delegates_to_engineer_and_continues_after_one_increment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    planner = _PlannerBackend([
        plan(summary='delegate the first bounded optimization', tasks=[{'key': 'first', 'title': 'Remove redundant snapshot prewarm', 'objective': 'Change the prewarm scheduling and add a regression test.', 'hypothesis': 'Duplicate prewarm work is the measured bottleneck.', 'goal_contribution': 'Remove wasted startup work from the user path.', 'expected_regressions': 'Startup ordering may change during the repair.', 'decision_rule': 'Revise if profiling shows prewarm is not on the critical path.', 'acceptance_check': 'pytest tests/webapi/test_index_cache.py'}]),
        plan(summary='continue the standing campaign with a distinct issue', tasks=[{'key': 'second', 'vertical': 'argus_maintenance', 'title': 'Deduplicate Manager reply rows', 'objective': 'Use one message identity for live and persisted replies.', 'hypothesis': 'Identity drift causes duplicate Manager reply rows.', 'goal_contribution': 'Make the user conversation readable and stable.', 'expected_regressions': 'Replay ordering may shift while identities converge.', 'decision_rule': 'Replace this route if duplicates persist with stable ids.', 'acceptance_check': 'npm test -- stream.test.ts'}]),
    ])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True
    first = supervisor.memory.backlog.pending()
    assert [item.title for item in first] == ["Remove redundant snapshot prewarm"]
    supervisor.memory.backlog.update(first[0].id, status="done")

    assert supervisor._plan_next_work() is True
    pending = supervisor.memory.backlog.pending()
    assert [item.title for item in pending] == ["Deduplicate Manager reply rows"]
    assert pending[0].manager_decision["vertical"] == "argus_maintenance"
    assert pending[0].manager_decision["route_source"] == "planner"
    assert "framework_maintenance" in pending[0].tags
    assert pending[0].context_refs == [{
        "ref": str(supervisor.memory.root / "events.jsonl"),
        "kind": "runtime evidence",
        "why": "Identity drift causes duplicate Manager reply rows.",
    }]

    assert len(planner.calls) == 2
    assert all(call["options"].sandbox_mode == "read-only" for call in planner.calls)
    assert all(call["options"].dangerous_yolo is False for call in planner.calls)
    assert not list(project.glob("**/*.py")), "Planner must not create implementation files"


@pytest.mark.parametrize("outcome", ["new_task", "retire_only", "waiting"])
def test_planner_retires_pending_tasks_without_requiring_new_work(
    tmp_path: Path,
    monkeypatch,
    caplog,
    outcome: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    planner = _PlannerBackend([])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    backlog = supervisor.memory.backlog
    pending = backlog.add(BacklogItem.new(title="Repair the refuted mechanism", objective="a"))
    running = backlog.add(BacklogItem.new(title="Work already running", objective="b"))
    backlog.mark_running(running.id)
    parked = backlog.add(BacklogItem.new(title="Waiting for a GPU job", objective="d"))
    backlog.update(
        parked.id,
        park_reason="external_work",
        outcome={"external_wait": {"work_id": "training", "workdir": str(project)}},
    )
    done = backlog.add(BacklogItem.new(title="Completed work", objective="c"))
    backlog.mark_done(done.id)
    reason = "The experiment refuted this mechanism family."
    planner.replies.append(plan(
        "operator" if outcome == "waiting" else "engineer",
        summary="Close the refuted line of work.",
        retire=[
            {"id": pending.id, "reason": reason},
            {"id": running.id, "reason": "This work already started."},
            {"id": parked.id, "reason": "This work is waiting for a GPU job."},
            {"id": done.id, "reason": "This work already finished."},
            {"id": "unknown", "reason": "This item no longer exists."},
        ],
        tasks=[{
            "key": "distinct", "title": "Test a distinct mechanism",
            "objective": "Run an experiment on the untested alternative.",
        }] if outcome == "new_task" else [],
        **({"ask": {"question": "Can you supply the new evidence?", "options": []}} if outcome == "waiting" else {}),
    ))

    with caplog.at_level("INFO", logger="argus.life.supervisor._planning_cycle_enqueue"):
        supervisor._plan_next_work()

    rows = {item.id: item for item in backlog.history()}
    assert rows[pending.id].status == "superseded"
    assert rows[pending.id].superseded_reason == reason
    assert rows[pending.id].superseded_by_plan_id
    assert rows[running.id].status == "running"
    assert rows[parked.id].status == "parked"
    assert rows[parked.id].outcome["external_wait"]["work_id"] == "training"
    assert rows[done.id].status == "done"
    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()
    ]
    retired = [event for event in events if event["type"] == EventType.LIFE_PLAN_NODE_SUPERSEDED]
    assert len(retired) == 1
    assert retired[0]["item_id"] == pending.id
    assert retired[0]["reason"] == reason
    assert retired[0]["source"] == "planner"
    assert retired[0]["superseded_by_plan_id"] == rows[pending.id].superseded_by_plan_id
    skipped = [record for record in caplog.records if "retirement skipped" in record.message]
    assert len(skipped) == 1
    assert all(
        item_id in skipped[0].message
        for item_id in (running.id, parked.id, done.id, "unknown")
    )
    assert f"{pending.id}: {pending.title}" in planner.calls[0]["prompt"]
    if outcome == "new_task":
        new_item, = backlog.pending()
        assert new_item.title == "Test a distinct mechanism"
        assert rows[pending.id].superseded_by_plan_id == new_item.plan_id
    else:
        assert backlog.pending() == []


def test_planner_reuses_front_door_route_without_manager_reclassification(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    planner = _PlannerBackend([
        plan(summary='delegate the next bounded repair', tasks=[{'key': 'repair', 'title': 'Repair the lifecycle', 'objective': 'Fix the lifecycle and run its focused test.'}])
    ])
    supervisor = _supervisor(project, life, planner)
    supervisor._vertical_resolved = False
    (life / "continuous.json").write_text(
        json.dumps({
            "enabled": True,
            "objective": "keep optimizing Argus",
            "generation": 1,
        }),
        encoding="utf-8",
    )
    (life / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "execution_task": "keep optimizing Argus",
            "continuous_generation": 1,
            "vertical": "software",
            "workflow_mode": "direct",
        }) + "\n",
        encoding="utf-8",
    )

    def fail_reclassification():
        raise AssertionError("front-door route must not be classified again")

    supervisor._resolve_vertical_once = fail_reclassification  # type: ignore[method-assign]

    assert supervisor._plan_next_work() is True
    assert supervisor.memory.backlog.pending()[0].manager_decision["vertical"] == "software"


@pytest.mark.parametrize("existing_status", [
    "pending", "running", "paused_external_work", "paused_operator", "research_incomplete",
])
def test_bounded_manager_direct_task_skips_planner_decomposition(
    tmp_path: Path,
    existing_status: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    planner = _PlannerBackend([])
    memory = LifeMemory.open(life)
    objective = "Fix src/parser.py and run python -m unittest."
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective=objective,
            open_ended=False,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    (life / "continuous.json").write_text(
        json.dumps({
            "enabled": True,
            "objective": objective,
            "generation": 1,
        }),
        encoding="utf-8",
    )
    (life / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "execution_task": objective,
            "continuous_generation": 1,
            "vertical": "software",
            "current_stage": "delivery",
            "workflow_mode": "direct",
        }) + "\n",
        encoding="utf-8",
    )

    assert supervisor._plan_next_work() is True

    pending = supervisor.memory.backlog.pending()
    assert len(pending) == 1
    assert pending[0].objective == objective
    assert "manager_direct" in pending[0].tags
    assert "stage_closing" in pending[0].tags
    assert "review:required" in pending[0].tags
    assert planner.calls == []

    # A later planning pass must not bootstrap the same finite objective again
    # while its original mission is running or waiting to resume.
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    memory.backlog.update(pending[0].id, status=existing_status)
    original = memory.backlog.history()
    state = _PlanCycleState(None)
    state.manager_intent = supervisor._manager_intent_context()
    assert supervisor._enqueue_bounded_manager_direct(state) is None
    assert memory.backlog.history() == original


def _kernel_supervisor(
    project: Path,
    life: Path,
    planner: _PlannerBackend,
) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="run the kernel algorithm campaign",
            open_ended=True,
            project_worktree=project,
            artifact_root=life,
        ),
        planner_runner=planner,
    )
    persist_vertical(life, "optimize_lab", workflow_mode="staged")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        add_dirs=[str(life)],
        open_ended=True,
    )
    return supervisor


def test_direct_kernel_workflow_does_not_generate_scope_bundle(
    optimize_vertical: str,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    planner = _PlannerBackend([
        plan(summary='run one direct measured optimization', tasks=[{'key': 'measure-hotpath', 'title': 'Measure and optimize the active MLX hotpath', 'objective': 'Profile the current path, implement one minimal candidate, and benchmark it.', 'hypothesis': 'The measured hotpath has one removable synchronization.', 'goal_contribution': 'Directly improve M4 Pro inference.', 'expected_regressions': 'Numerical parity and memory pressure may regress.', 'decision_rule': 'Retain only if the interleaved benchmark improves.', 'acceptance_check': 'python scripts/benchmark_hotpath.py'}])
    ])
    supervisor = _kernel_supervisor(project, life, planner)
    persist_vertical(life, optimize_vertical, workflow_mode="direct")

    assert supervisor._plan_next_work() is True

    assert len(planner.calls) == 1
    pending = supervisor.memory.backlog.pending()
    assert [item.title for item in pending] == [
        "Measure and optimize the active MLX hotpath"
    ]
    assert "KERNEL_SCOPE.md" not in pending[0].objective
    assert "stage_closing" not in pending[0].tags


def test_manager_intent_survives_generation_only_daemon_restart(
    tmp_path: Path,
) -> None:
    from argus.life.supervisor._planning_context import PlanningContextMixin

    life = tmp_path / "life"
    life.mkdir()
    (life / "continuous.json").write_text(
        json.dumps({
            "enabled": True,
            "objective": "optimize MLX inference",
            "generation": 4,
        }),
        encoding="utf-8",
    )
    (life / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "execution_task": "optimize MLX inference",
            "continuous_generation": 2,
            "vertical": "apple_mlx_inference",
            "workflow_mode": "staged",
            "learned_vertical_status": "candidate",
            "reason": "The repository uses one measured MLX hot path.",
        }) + "\n",
        encoding="utf-8",
    )

    class Harness(PlanningContextMixin):
        memory = SimpleNamespace(root=life)
        config = SimpleNamespace(project_state_dir=life)

        @staticmethod
        def _current_pipeline_stage() -> str:
            return "hotpath_profile"

    assert Harness()._manager_intent_context() == {
        "execution_task": "optimize MLX inference",
        "vertical": "apple_mlx_inference",
        "workflow_mode": "staged",
        "learned_vertical_status": "candidate",
        "reason": "The repository uses one measured MLX hot path.",
        "continuous_generation": 2,
        "stage": "hotpath_profile",
        "current_stage": "hotpath_profile",
    }

    prompt_block = Harness._manager_intent_prompt_block(
        Harness()._manager_intent_context(),
        "optimize MLX inference",
    )
    assert prompt_block.startswith("## Manager routing boundary (authoritative)")
    assert "VERTICAL=apple_mlx_inference" in prompt_block
    assert "WORKFLOW=staged" in prompt_block
    assert "AUTHORITY=technical" in prompt_block
    assert "## Manager strategic context" in prompt_block
    assert "one measured MLX hot path" in prompt_block
    assert "intent_id" not in prompt_block


def test_continuous_reload_updates_lifetime_and_final_gate() -> None:
    from argus.life.supervisor._planning_context import PlanningContextMixin

    class Harness(PlanningContextMixin):
        runner = SimpleNamespace(_args=SimpleNamespace(
            open_ended=False, continuous_objective="old",
        ))
        config = SimpleNamespace(
            continuous=False,
            continuous_objective="old",
            open_ended=False,
            paper_mission=True,
            final_certification_gate=False,
            continuous_config_provider=lambda: (
                True,
                "standing paper",
                True,
            ),
        )

    harness = Harness()
    harness._reload_continuous_config()

    assert harness.config.continuous is True
    assert harness.config.continuous_objective == "standing paper"
    assert harness.config.open_ended is True
    assert harness.config.final_certification_gate is True
    assert harness.runner._args.open_ended is True
    assert harness.runner._args.continuous_objective == "standing paper"

    harness.config.continuous_config_provider = lambda: (False, "", True)
    harness._reload_continuous_config()
    assert harness.config.open_ended is True
    assert harness.config.final_certification_gate is False
    assert harness.runner._args.open_ended is False
    assert harness.runner._args.continuous_objective == ""

    harness.config.continuous_config_provider = lambda: (True, "finite study", False)
    harness._reload_continuous_config()
    assert harness.runner._args.open_ended is False
    assert harness.runner._args.continuous_objective == "finite study"


def test_continuous_reload_rejects_malformed_provider_tuple(caplog) -> None:
    from argus.life.supervisor._planning_context import PlanningContextMixin

    class Harness(PlanningContextMixin):
        config = SimpleNamespace(
            continuous=True,
            continuous_objective="current",
            open_ended=False,
            paper_mission=False,
            final_certification_gate=False,
            continuous_config_provider=lambda: (False, ""),
        )

    harness = Harness()
    harness._reload_continuous_config()

    assert harness.config.continuous is True
    assert harness.config.continuous_objective == "current"
    assert harness.config.open_ended is False
    assert "must return (enabled, objective, open_ended)" in caplog.text


def test_task_policy_uses_isolated_stage_and_execution_evidence_root(
    optimize_vertical: str,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    research = project / "research"
    (research / "frontier").mkdir(parents=True)
    (research / "KERNEL_SCOPE.md").write_text("# scope\n", encoding="utf-8")
    (research / "PROJECT_NATIVE_SETUP.md").write_text("# setup\n", encoding="utf-8")
    (research / "frontier" / "scope.json").write_text("{}\n", encoding="utf-8")
    # Deliberately stale workspace state must not override Manager-owned state.
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": optimize_vertical, "current_stage": "optimize"}),
        encoding="utf-8",
    )
    attempt = project / "attempts" / "winner"
    attempt.mkdir(parents=True)
    (attempt / "OUTCOME.json").write_text(
        json.dumps({
            "attempt_id": "winner",
            "execution_status": "completed",
            "failure_class": "none",
            "idea_status": "supported",
        }),
        encoding="utf-8",
    )
    (research / "PERFORMANCE_RESULT.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )
    planner = _PlannerBackend([
        plan(summary='finish the authoritative scope stage', tasks=[{'key': 'scope-repair', 'title': 'Reconcile the scope contract', 'objective': 'Repair the current scope evidence only.', 'hypothesis': 'The scope contract has one remaining inconsistency.', 'goal_contribution': 'Make the campaign ready for discovery.', 'expected_regressions': 'None; implementation is unchanged.', 'decision_rule': 'Stop if the scope is already internally consistent.', 'acceptance_check': 'scope artifacts agree'}])
    ])
    supervisor = _kernel_supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True

    assert len(planner.calls) == 1
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Reconcile the scope contract"
    ]


def test_planner_receives_host_current_reality_without_rediscovery(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    checkpoint = project / "CHECKPOINT.md"
    checkpoint.write_text(
        "# Open Questions / Blockers\n\n- verify the production artifact\n",
        encoding="utf-8",
    )
    supervisor.memory.backlog.add(BacklogItem.new(
        title="pending repair",
        objective="repair the active path",
    ))

    note = supervisor._planner_current_reality_note()

    assert note.count("## Host current-reality digest") == 1
    assert "- vertical: software" in note
    assert "- current_stage:" in note
    assert '"pending": 1' in note
    assert "git_changed_paths" in note
    assert "verify the production artifact" in note
    assert "Do not spend tools rereading those sources" in note


def test_0d3_later_no_gap_evidence_replaces_skip_zero_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    planner = _PlannerBackend([
        plan(summary='replace the refuted candidate with the better validator route', tasks=[{'key': 'no-gap', 'title': 'Adopt the no-gap validator', 'objective': 'Implement and verify the no-gap validator alternative.', 'hypothesis': 'The no-gap validator meets the user goal without skip-zero.', 'goal_contribution': 'Remove an unnecessary candidate constraint.', 'expected_regressions': 'The old skip-zero checker may become obsolete.', 'decision_rule': 'Abandon if no-gap fails the original user-level property.', 'acceptance_check': 'Verify the original user property with no-gap enabled.'}])
    ])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    trigger = supervisor.memory.backlog.add(BacklogItem.new(
        title="Generate skip-zero candidate",
        objective="Generate and validate the preselected skip-zero candidate.",
        item_id="skip-zero",
        plan_id="plan-old",
        plan_version=1,
        node_key="skip-zero",
    ))
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Continue skip-zero rollout",
        objective="Roll out the skip-zero candidate.",
        item_id="skip-zero-rollout",
        plan_id="plan-old",
        plan_version=1,
        node_key="rollout",
        deps=[trigger.id],
    ))
    outcome = {
        "item_id": trigger.id,
        "status": "planner",
        "review_status": "done",
        "review_reason": "The no-gap validator dominates the preselected candidate.",
        "expected_plan_id": "plan-old",
        "expected_plan_version": 1,
        "plan_challenge": {
            "manager_action": "replace",
            "manager_reason": "Later evidence supports a concrete alternative.",
            "challenge": "The preselected skip-zero candidate is unnecessary.",
            "alternative": "Use the no-gap validator alternative.",
            "source": "manager_authority_policy",
            "raised_at": time.time() - 2,
        },
    }

    assert supervisor._adjudicate_mission_challenge(outcome) == "replace"
    assert supervisor._plan_next_work(revision_request=outcome) is True

    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows["skip-zero"].status == "superseded"
    assert rows["skip-zero-rollout"].status == "superseded"
    replacement = next(item for item in rows.values() if item.status == "pending")
    assert replacement.title == "Adopt the no-gap validator"
    assert replacement.plan_hypothesis.startswith("The no-gap validator")
    assert replacement.decision_rule.startswith("Abandon if no-gap fails")
    assert replacement.iterate is True
    prompt = planner.calls[0]["prompt"]
    assert "challenged_assumption: The preselected skip-zero candidate" in prompt
    assert "proposed_alternative: Use the no-gap validator" in prompt
    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()
    ]
    decided = [
        event for event in events
        if event.get("type") == "life.manager.plan_challenge.decided"
    ]
    committed = [
        event for event in events
        if event.get("type") == "life.plan.revision.committed"
    ]
    assert decided and decided[-1]["manager_action"] == "replace"
    assert decided[-1]["revision_latency_seconds"] >= 1
    assert committed and committed[-1]["alternative"].startswith("Use the no-gap")


def _assert_replan_replace_commits_after_source_terminalized(
    tmp_path: Path,
    monkeypatch,
    *,
    plan_version: int,
    expected_replacement_version: int,
) -> None:
    class _TerminalizedReplanRunner:
        def execute(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(
                success=False,
                status="planner",
                stop_reason="completed work requires a replacement plan",
                rounds=1,
                final_review_status="done",
                final_review_reason=(
                    "The source node completed enough evidence to refute itself."
                ),
                plan_challenge={
                    "manager_action": "replace",
                    "manager_reason": "Later evidence supports a replacement.",
                    "challenge": "The source route is now worse than the replacement.",
                    "alternative": "Commit the replacement route.",
                    "source": "manager_authority_policy",
                    "raised_at": time.time() - 2,
                },
                stage_transition={},
            )

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    planner = _PlannerBackend([
        plan(summary='replace the terminalized source with the approved route', tasks=[{'key': 'replacement', 'title': 'Commit the replacement route', 'objective': 'Implement the replacement route approved by Manager.', 'hypothesis': 'The replacement route satisfies the original goal.', 'goal_contribution': 'Preserve progress after the source refuted itself.', 'expected_regressions': 'The original route is superseded.', 'decision_rule': 'Reject if the replacement does not meet the old goal.', 'acceptance_check': 'Verify the original goal on the replacement route.'}])
    ])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    supervisor.runner = _TerminalizedReplanRunner()
    trigger = supervisor.memory.backlog.add(BacklogItem.new(
        title="Run the source route",
        objective="Run the source route until its evidence is decisive.",
        item_id="source-route",
        plan_id="plan-old",
        plan_version=plan_version,
        node_key="source",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Continue the source route",
        objective="Continue after the source route.",
        item_id="source-followup",
        plan_id="plan-old",
        plan_version=plan_version,
        node_key="followup",
        deps=[trigger.id],
    ))

    outcome = supervisor._run_one(trigger)

    rows_after_run = {item.id: item for item in supervisor.memory.backlog.all()}
    # The Manager's ruling on the challenge, not a progress bit, retires it.
    assert rows_after_run["source-route"].status == "pending"
    assert outcome["status"] == "planner"
    assert outcome["plan_revision_witness"]["active_item_ids"] == [
        "source-route",
        "source-followup",
    ]
    assert supervisor._adjudicate_mission_challenge(outcome) == "replace"
    assert supervisor._plan_next_work(revision_request=outcome) is True

    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows["source-route"].status == "superseded"
    assert rows["source-followup"].status == "superseded"
    replacement = next(item for item in rows.values() if item.status == "pending")
    assert replacement.title == "Commit the replacement route"
    assert replacement.plan_version == expected_replacement_version
    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()
    ]
    committed = [
        event for event in events
        if event.get("type") == "life.plan.revision.committed"
    ]
    assert committed and committed[-1]["superseded_item_ids"] == [
        "source-route",
        "source-followup",
    ]


def test_replan_replace_commits_after_version_zero_source_terminalized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _assert_replan_replace_commits_after_source_terminalized(
        tmp_path,
        monkeypatch,
        plan_version=0,
        expected_replacement_version=1,
    )


def test_new_continuous_generation_interrupts_obsolete_planner(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    memory = LifeMemory.open(life)
    write_continuous_config(life, enabled=True, objective="old objective")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="old objective",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=object(),
    )

    config = supervisor._planner_config()
    provider = config.external_interrupt_reason_provider
    assert provider() is None
    assert config.state_root == str(project)
    assert config.add_dirs == []

    write_continuous_config(life, enabled=True, objective="new operator objective")

    assert provider() == "planner superseded by newer continuous generation"


def test_research_planner_is_granted_the_playbook_its_prompt_names(
    tmp_path: Path,
) -> None:
    import re

    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    memory = LifeMemory.open(life)
    write_continuous_config(life, enabled=True, objective="find a general method")
    persist_vertical(project, "research")
    state = project / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["current_stage"] = "experiment"
    state.write_text(json.dumps(payload), encoding="utf-8")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="find a general method",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=object(),
    )

    config = supervisor._planner_config()
    prompt = Planner._build_planner_prompt(
        continuous_objective="find a general method",
        journal_tail="(empty)",
        planning_cycle=0,
        open_ended=True,
        project_root=project,
        state_root=config.state_root,
    )

    named = Path(re.search(r"Open `([^`]+research-experiment-playbook\.md)`", prompt).group(1))
    assert named.is_file()
    assert str(named.parent) in config.add_dirs
    assert str(named.parents[3]) not in config.add_dirs
