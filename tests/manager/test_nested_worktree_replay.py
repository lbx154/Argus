"""Backlog nodes an older release decomposed into a nested repository keep
replaying from their validated root after dispatch collapsed into one path."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime import _SkillLoopRunner
from argus.core.campaign_workdir import adopt_campaign_workdir
from argus.core.file_facts import file_facts
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.stage_certificate import latest_stage_review
from argus.life import BacklogItem, MemoryBundle
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.loop import SkillLoopConfig
from argus.manager import front_door
from argus.skills.stage_machine import completion_contract_fingerprint
from argus.skills.vertical_select import persist_vertical, resolve_vertical
from argus.verticals._data_domain import write_data_domain


class _Sink:
    def handle_event(self, event):  # noqa: ANN001
        return None


@pytest.mark.parametrize(
    "case",
    ["existing", "deferred", "adopted_campaign", "external_symlink"],
)
def test_legacy_node_workdir_provenance_replays_from_its_validated_root(
    tmp_path,
    case,
) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    memory = MemoryBundle.for_cwd(
        base,
        global_root=tmp_path / "state",
        fingerprint=f"s-workdir-{case}",
    )
    memory.init()
    life_dir = Path(front_door._life_dir_for(memory))
    anchor = base
    requested = case
    target = base / requested
    deferred = case == "deferred"

    if case == "adopted_campaign":
        anchor = base / "campaign"
        anchor.mkdir()
        subprocess.run(["git", "init", "-q", str(anchor)], check=True)
        adopt_campaign_workdir(
            state_root=life_dir,
            base_root=base,
            current_root=base,
            requested="campaign",
        )
        requested = "target"
        target = anchor / requested
    elif case == "external_symlink":
        target = tmp_path / "external-repository"
        requested = "repository-link"
        try:
            (base / requested).symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege is unavailable")
            raise

    if not deferred:
        target.mkdir()
        subprocess.run(["git", "init", "-q", str(target)], check=True)

    item = memory.backlog.add(BacklogItem.new(
        title="Run in selected repository",
        objective="Use the selected repository.",
        tags=["manager", "planner", "bounded_dag_node", "scope:bounded"],
        plan_id="bounded-legacy",
        plan_version=1,
        node_key="run",
        execution_workdir=str(anchor / requested),
    ))

    if deferred:
        target.mkdir()
        subprocess.run(["git", "init", "-q", str(target)], check=True)

    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.memory = memory
    supervisor.config = SimpleNamespace(project_worktree=base)
    supervisor._emit_status = lambda _message: None

    assert supervisor._resolve_mission_workdir(item) == target.resolve()


class _ProductionRunnerProbe(_SkillLoopRunner):
    """Run the production execute/config/stage path without model backends."""

    def __init__(self, *, workdir: Path, artifact_root: Path, manager) -> None:  # noqa: ANN001
        self._args = SimpleNamespace(
            workdir=str(workdir),
            project_state_dir=str(artifact_root),
            engineer_model="",
            reviewer_model="",
            max_rounds=1,
            paper_mission=None,
            open_ended=False,
            continuous_objective="",
        )
        self._artifact_root = artifact_root
        self._manager_session_root = artifact_root
        self.manager = manager
        self._SkillLoopConfig = SkillLoopConfig
        self._allow_chat_fast_path = False
        self._role_memory_maintenance_enabled = False
        self._active_usage_mission_id = None
        self.config = None
        self.workdir = None
        self.prelude_context = ""
        self.context_packet_path = ""
        self.policy_root_seen = None

    def _build_execute_skill_store_and_loop(self, ex_state, *, sink):  # noqa: ANN001, ARG002
        return None

    def _prepare_execute_mission_context(
        self,
        ex_state,
        *,
        objective,
        review_objective,
        prelude_context,
        seed_thread_id,
        scope,
    ):  # noqa: ANN001, ARG002
        ex_state.mission_scope = scope
        self.config = ex_state.config
        self.workdir = ex_state.workdir
        self.prelude_context = prelude_context
        self.context_packet_path = str(ex_state.config.context_packet_path)
        self.policy_root_seen = Path(self._artifact_root).resolve()

    def _invoke_execute_loop(self, ex_state, **kwargs):  # noqa: ANN001, ANN003, ARG002
        ex_state.outcome = SimpleNamespace(
            success=True,
            status="done",
            reason="",
            stop_kind=None,
            recoverable=False,
        )

    def _extract_execute_outcome_fields(self, ex_state):  # noqa: ANN001
        review = SimpleNamespace(
            status="done",
            reason="",
            review_source="reviewer",
            next_action="",
            operator_question="",
            operator_options=[],
        )
        ex_state.rounds_list = [SimpleNamespace(review=review)]
        ex_state.review_source = "reviewer"
        ex_state.final_review_status = "done"

    def _build_execute_outcome(self, ex_state):  # noqa: ANN001
        return SimpleNamespace(
            success=True,
            status=ex_state.effective_status,
            stop_reason=ex_state.effective_reason,
            stop_kind=ex_state.effective_stop_kind,
            recoverable=ex_state.effective_recoverable,
            rounds=1,
            final_review_status="done",
            stage_transition=ex_state.stage_transition,
        )

    def _set_usage_context(self, mission_id):  # noqa: ANN001, ARG002
        return None


def test_legacy_nested_node_uses_its_own_contract_root(
    tmp_path,
    monkeypatch,
) -> None:
    base = tmp_path / "workspace"
    campaign = base / "campaign"
    target = campaign / "target"
    campaign.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(campaign)], check=True)
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    memory = MemoryBundle.for_cwd(
        base,
        global_root=tmp_path / "state",
        fingerprint="s-nested-replay",
    )
    memory.init()
    life_dir = Path(front_door._life_dir_for(memory))
    adopt_campaign_workdir(
        state_root=life_dir,
        base_root=base,
        current_root=base,
        requested="campaign",
    )

    # The stable core state and parent campaign deliberately carry stale,
    # conflicting routing. They must remain durable state, not task policy.
    for root in (life_dir, campaign):
        persist_vertical(root, "research", workflow_mode="staged")
        write_pipeline_state(
            root,
            {
                "vertical": "research",
                "workflow_mode": "staged",
                "current_stage": "submission",
            },
        )

    write_data_domain(
        target,
        "nested_replay",
        stages=["target_scope", "target_delivery"],
        status="formal",
        purpose="target-only replay policy",
    )
    persist_vertical(target, "nested_replay", workflow_mode="staged")
    artifact = target / "research" / "TARGET.md"
    artifact.write_text("target evidence", encoding="utf-8")
    parent_artifact = campaign / "research" / "TARGET.md"
    parent_artifact.parent.mkdir(exist_ok=True)
    parent_artifact.write_text("stale parent evidence", encoding="utf-8")

    class _Manager:
        def __init__(self) -> None:
            self.bound_workdirs: list[Path] = []
            self.stage_roots: list[Path] = []

        def bind_execution_workdir(self, workdir):  # noqa: ANN001
            self.bound_workdirs.append(Path(workdir).resolve())
            return self

        def decide_stage_transition(self, *, project_root, **kwargs):  # noqa: ANN001, ANN003
            root = Path(project_root).resolve()
            self.stage_roots.append(root)
            state = read_pipeline_state(root)
            assert state["current_stage"] == "target_scope"
            write_pipeline_state(root, {**state, "current_stage": "target_delivery"})
            return SimpleNamespace(
                action="advance",
                target_stage="target_delivery",
                reason="production runner policy-root replay",
                current_stage="target_scope",
                source="test_manager",
                diagnostic="",
            )

    first = BacklogItem.new(
        title="Run nested replay",
        objective="Use the target policy and artifact.",
        tags=[
            "manager", "planner", "bounded_dag_node", "scope:bounded",
            "stage_closing", "review:required", "stage:target_scope",
        ],
        plan_id="bounded-legacy",
        plan_version=1,
        node_key="target-node",
        execution_workdir=str(target.resolve()),
        context_refs=[{
            "kind": "artifact",
            "ref": "research/TARGET.md",
            "why": "target evidence",
            "content_hash": json.dumps(file_facts(artifact)),
        }],
        manager_decision={"routed": True, "vertical": "nested_replay", "route_source": "planner"},
    )
    memory.backlog.add_many([
        first,
        BacklogItem.new(
            title="Finish nested replay",
            objective="Finish only after the target stage attempt settles.",
            tags=[
                "manager", "planner", "bounded_dag_node", "scope:bounded",
                "review:required", "stage:target_scope",
            ],
            plan_id="bounded-legacy",
            plan_version=1,
            node_key="dependent-node",
            deps=[first.id],
            execution_workdir=str(target.resolve()),
            manager_decision={"routed": True, "vertical": "nested_replay", "route_source": "planner"},
        ),
    ])

    policy_calls: list[dict[str, object]] = []

    def _capture_policy(**kwargs):  # noqa: ANN003
        vertical_root = Path(kwargs["vertical_root"])
        project_root = Path(kwargs["project_root"])
        stage = str(kwargs["stage"])
        policy_calls.append({
            "vertical_root": vertical_root.resolve(),
            "project_root": project_root.resolve(),
            "vertical": resolve_vertical(vertical_root),
            "stage": stage,
        })
        evidence = (project_root / "research" / "TARGET.md").read_text(
            encoding="utf-8"
        )
        return f"POLICY vertical={resolve_vertical(vertical_root)} stage={stage} artifact={evidence}"

    monkeypatch.setattr(
        "argus.verticals._base.vertical_mission_prelude",
        _capture_policy,
    )

    mission_manager = _Manager()
    runner = _ProductionRunnerProbe(
        workdir=base,
        artifact_root=life_dir,
        manager=mission_manager,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=base,
            artifact_root=life_dir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert policy_calls == [{
        "vertical_root": target.resolve(),
        "project_root": target.resolve(),
        "vertical": "nested_replay",
        "stage": "target_scope",
    }]
    assert runner.workdir == target.resolve()
    assert runner.policy_root_seen == target.resolve()
    assert Path(runner.config.vertical_state_root).resolve() == target.resolve()
    assert runner.config.active_vertical == "nested_replay"
    assert all(root == target.resolve() for root in mission_manager.bound_workdirs)
    assert mission_manager.stage_roots == [target.resolve()]
    assert Path(runner._artifact_root).resolve() == life_dir.resolve()
    assert "artifact=target evidence" in runner.prelude_context
    packet = json.loads(
        Path(runner.context_packet_path).read_text(encoding="utf-8")
    )
    assert packet["stage"] == "target_scope"
    assert packet["execution_workdir"] == str(target.resolve())
    assert read_pipeline_state(target)["current_stage"] == "target_scope"
    assert read_pipeline_state(campaign)["current_stage"] == "submission"
    assert read_pipeline_state(life_dir)["current_stage"] == "submission"
    certificate = latest_stage_review(memory.root, "target_scope")
    assert certificate is not None
    assert certificate["manager_action"] == "hold"
    assert certificate["checklist_fingerprint"] == completion_contract_fingerprint(
        target,
        "target_scope",
        version=1,
    )
