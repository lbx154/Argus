"""Planner must delegate implementation and keep standing campaigns moving."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.daemon.state import write_continuous_config
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import (
    BacklogItem,
    GlobalMemory,
    LifeMemory,
    MemoryBundle,
    ProjectMemory,
)
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.planner import Planner, PlannerConfig
from argus_skill.skills.vertical_select import persist_vertical


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


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
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=delegate the first bounded optimization",
            "TASK_KEY=first",
            "TASK_TITLE=Remove redundant snapshot prewarm",
            "TASK_OBJECTIVE=Change the prewarm scheduling and add a regression test.",
            "TASK_HYPOTHESIS=Duplicate prewarm work is the measured bottleneck.",
            "TASK_GOAL_CONTRIBUTION=Remove wasted startup work from the user path.",
            "TASK_EXPECTED_REGRESSIONS=Startup ordering may change during the repair.",
            "TASK_DECISION_RULE=Revise if profiling shows prewarm is not on the critical path.",
            "TASK_ACCEPTANCE_CHECK=pytest tests/webapi/test_index_cache.py",
        ]),
        "PROJECT_DONE=true\nREASON=finished one optimization",
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=continue the standing campaign with a distinct issue",
            "TASK_KEY=second",
            "TASK_VERTICAL=argus_maintenance",
            "TASK_TITLE=Deduplicate Manager reply rows",
            "TASK_OBJECTIVE=Use one message identity for live and persisted replies.",
            "TASK_HYPOTHESIS=Identity drift causes duplicate Manager reply rows.",
            "TASK_GOAL_CONTRIBUTION=Make the user conversation readable and stable.",
            "TASK_EXPECTED_REGRESSIONS=Replay ordering may shift while identities converge.",
            "TASK_DECISION_RULE=Replace this route if duplicates persist with stable ids.",
            "TASK_ACCEPTANCE_CHECK=npm test -- stream.test.ts",
        ]),
    ])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True
    first = supervisor.memory.backlog.pending()
    assert [item.title for item in first] == ["Remove redundant snapshot prewarm"]
    supervisor.memory.backlog.update(first[0].id, status="done")

    assert supervisor._plan_next_work() == PLAN_RETRY
    assert supervisor._plan_next_work() is True
    pending = supervisor.memory.backlog.pending()
    assert [item.title for item in pending] == ["Deduplicate Manager reply rows"]
    assert pending[0].manager_decision["vertical"] == "argus_maintenance"
    assert pending[0].manager_decision["route_source"] == "planner"

    assert len(planner.calls) == 3
    assert all(call["options"].sandbox_mode == "read-only" for call in planner.calls)
    assert all(call["options"].dangerous_yolo is False for call in planner.calls)
    assert not list(project.glob("**/*.py")), "Planner must not create implementation files"


def test_planner_reuses_front_door_route_without_manager_reclassification(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=delegate the next bounded repair",
            "TASK_KEY=repair",
            "TASK_TITLE=Repair the lifecycle",
            "TASK_OBJECTIVE=Fix the lifecycle and run its focused test.",
        ])
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


def test_bounded_manager_direct_task_skips_planner_decomposition(
    tmp_path: Path,
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
    assert "review:required" not in pending[0].tags
    assert planner.calls == []


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
    persist_vertical(life, "kernel_engineering", workflow_mode="staged")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        add_dirs=[str(life)],
        open_ended=True,
    )
    return supervisor


def test_direct_kernel_workflow_does_not_generate_scope_bundle(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=run one direct measured optimization",
            "TASK_KEY=measure-hotpath",
            "TASK_TITLE=Measure and optimize the active MLX hotpath",
            "TASK_OBJECTIVE=Profile the current path, implement one minimal candidate, and benchmark it.",
            "TASK_HYPOTHESIS=The measured hotpath has one removable synchronization.",
            "TASK_GOAL_CONTRIBUTION=Directly improve M4 Pro inference.",
            "TASK_EXPECTED_REGRESSIONS=Numerical parity and memory pressure may regress.",
            "TASK_DECISION_RULE=Retain only if the interleaved benchmark improves.",
            "TASK_ACCEPTANCE_CHECK=python scripts/benchmark_hotpath.py",
        ])
    ])
    supervisor = _kernel_supervisor(project, life, planner)
    persist_vertical(life, "kernel_engineering", workflow_mode="direct")

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
    from argus_skill.life.supervisor._planning_context import PlanningContextMixin

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
    from argus_skill.life.supervisor._planning_context import PlanningContextMixin

    class Harness(PlanningContextMixin):
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


def test_task_policy_uses_isolated_stage_and_execution_evidence_root(
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
        json.dumps({"vertical": "kernel_engineering", "current_stage": "optimize"}),
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
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=finish the authoritative scope stage",
            "TASK_KEY=scope-repair",
            "TASK_TITLE=Reconcile the scope contract",
            "TASK_OBJECTIVE=Repair the current scope evidence only.",
            "TASK_HYPOTHESIS=The scope contract has one remaining inconsistency.",
            "TASK_GOAL_CONTRIBUTION=Make the campaign ready for discovery.",
            "TASK_EXPECTED_REGRESSIONS=None; implementation is unchanged.",
            "TASK_DECISION_RULE=Stop if the scope is already internally consistent.",
            "TASK_ACCEPTANCE_CHECK=scope artifacts agree",
        ])
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
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=replace the refuted candidate with the better validator route",
            "TASK_KEY=no-gap",
            "TASK_TITLE=Adopt the no-gap validator",
            "TASK_OBJECTIVE=Implement and verify the no-gap validator alternative.",
            "TASK_HYPOTHESIS=The no-gap validator meets the user goal without skip-zero.",
            "TASK_GOAL_CONTRIBUTION=Remove an unnecessary candidate constraint.",
            "TASK_EXPECTED_REGRESSIONS=The old skip-zero checker may become obsolete.",
            "TASK_DECISION_RULE=Abandon if no-gap fails the original user-level property.",
            "TASK_ACCEPTANCE_CHECK=Verify the original user property with no-gap enabled.",
        ])
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
        "status": "replan_requested",
        "review_status": "done",
        "review_reason": "The no-gap validator dominates the preselected candidate.",
        "expected_plan_id": "plan-old",
        "expected_plan_version": 1,
        "planner_report": {
            "plan_signal": "reconsider",
            "challenge": "The preselected skip-zero candidate is unnecessary.",
            "alternative": "Use the no-gap validator alternative.",
            "authority_impact": "technical",
        },
        "plan_challenge": {
            "manager_action": "replace",
            "manager_reason": "Later evidence supports a concrete alternative.",
            "challenge": "The preselected skip-zero candidate is unnecessary.",
            "alternative": "Use the no-gap validator alternative.",
            "authority_impact": "technical",
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


def test_forbidden_questions_request_revision_within_existing_authority(
    tmp_path: Path,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life", _PlannerBackend([]))
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="Repair the environment",
        objective="Resolve the reversible environment issue.",
    ))
    set_active_manager_directive(
        supervisor.memory.root,
        "continue without asking",
        operator_question_policy="forbid",
    )
    outcome = {
        "item_id": item.id,
        "status": "replan_requested",
        "operator_question": "Choose the environment route.",
        "operator_options": [{"id": "yes"}],
        "planner_report": {
            "plan_signal": "reconsider",
            "challenge": "The current environment path failed.",
            "alternative": "Use the available local toolchain.",
            "authority_impact": "operator",
            "operator_question": "Choose the environment route.",
        },
        "plan_challenge": {
            "manager_action": "ask_operator",
            "manager_reason": "The Reviewer assigned this to the operator.",
            "challenge": "The current environment path failed.",
            "alternative": "Use the available local toolchain.",
            "authority_impact": "operator",
            "operator_question": "Choose the environment route.",
            "operator_options": [{"id": "yes"}],
            "raised_at": time.time(),
        },
    }

    action = supervisor._adjudicate_mission_challenge(outcome)

    assert action == "revise"
    assert outcome["operator_question"] == ""
    assert outcome["operator_options"] == []
    assert outcome["planner_report"]["authority_impact"] == "operator"
    assert "alternative" not in outcome["planner_report"]
    challenge = outcome["plan_challenge"]
    assert challenge["manager_action"] == "revise"
    assert challenge["authority_impact"] == "operator"
    assert challenge["alternative"] == ""
    assert challenge["operator_question"] == ""
    assert challenge["operator_options"] == []
    persisted = next(
        row for row in supervisor.memory.backlog.all() if row.id == item.id
    )
    assert persisted.status == "pending"
    assert persisted.pending_question == ""
    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()
    ]
    assert not any(
        event["type"] == "life.operator_question.pending" for event in events
    )


@pytest.mark.parametrize(
    "alternative",
    [
        "Use the operator's API credentials.",
        "Purchase additional compute capacity.",
        "Force-push the protected release branch.",
    ],
    ids=["credentials", "spending", "irreversible"],
)
def test_forbidden_questions_block_out_of_scope_operator_alternative(
    tmp_path: Path,
    alternative: str,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life", _PlannerBackend([]))
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="Operator-owned boundary",
        objective="Proceed only within existing authority.",
    ))
    set_active_manager_directive(
        supervisor.memory.root,
        "continue without asking",
        operator_question_policy="forbid",
    )
    outcome = {
        "item_id": item.id,
        "status": "replan_requested",
        "operator_question": "Choose the requested operator-owned action.",
        "operator_options": [{"id": "approve"}],
        "planner_report": {
            "plan_signal": "reconsider",
            "challenge": "The current plan reaches an operator-owned boundary.",
            "alternative": alternative,
            "authority_impact": "operator",
        },
        "plan_challenge": {
            "manager_action": "ask_operator",
            "manager_reason": "The alternative requires operator authority.",
            "challenge": "The current plan reaches an operator-owned boundary.",
            "alternative": alternative,
            "authority_impact": "operator",
            "operator_question": "Choose the requested operator-owned action.",
            "operator_options": [{"id": "approve"}],
            "raised_at": time.time(),
        },
    }

    action = supervisor._adjudicate_mission_challenge(outcome)

    assert action == "blocked"
    assert outcome["status"] == "blocked"
    assert outcome["operator_question"] == ""
    assert outcome["operator_options"] == []
    assert outcome["planner_report"]["authority_impact"] == "operator"
    assert "alternative" not in outcome["planner_report"]
    challenge = outcome["plan_challenge"]
    assert challenge["manager_action"] == "blocked"
    assert challenge["authority_impact"] == "operator"
    assert challenge["alternative"] == ""
    assert challenge["operator_question"] == ""
    persisted = next(
        row for row in supervisor.memory.backlog.all() if row.id == item.id
    )
    assert persisted.status == "failed"
    assert persisted.pending_question == ""
    assert persisted.operator_decision == {}
    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()
    ]
    assert not any(
        event["type"] == "life.operator_question.pending" for event in events
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


def test_split_memory_planner_resolves_vertical_from_project_state(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    global_root = tmp_path / "state"
    project_memory = ProjectMemory.open(
        "s-kernel",
        global_root=global_root,
    )
    memory = MemoryBundle(
        global_mem=GlobalMemory.open(global_root),
        project=project_memory,
        project_worktree=worktree,
    )
    memory.init()
    write_continuous_config(
        project_memory.root,
        enabled=True,
        objective="optimize full-model inference serving",
    )
    persist_vertical(
        project_memory.root,
        "kernel_engineering",
        workflow_mode="direct",
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=project_memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="optimize full-model inference serving",
            open_ended=True,
            project_worktree=worktree,
            artifact_root=project_memory.root,
        ),
        planner_runner=object(),
    )

    config = supervisor._planner_config()
    prompt = Planner._build_planner_prompt(
        continuous_objective="optimize full-model inference serving",
        journal_tail="(empty)",
        planning_cycle=0,
        open_ended=True,
        project_root=worktree,
        state_root=config.state_root,
    )

    assert config.state_root == str(project_memory.root)
    assert config.role_session_path == project_memory.root / "role-sessions" / "planner.json"
    assert str(global_root) not in config.add_dirs
    assert "fill spare mission slots" in prompt
