from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from argus_skill.core.event_catalog import validate_event_envelope
from argus_skill.life import BacklogItem, MemoryBundle
from argus_skill.manager import dispatch, front_door


@pytest.fixture()
def memory(tmp_path):
    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-dispatch01",
    )
    mem.init()
    return mem


@pytest.fixture(autouse=True)
def manager_runner(monkeypatch):
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(execution_task=f"managed: {body}")

        def commit_vertical_decision(self, body, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=Manager()),
    )


def test_bounded_dispatch_persists_parsed_work_kind_and_nested_workdir(
    memory,
    monkeypatch,
):
    from argus_skill.core.models import RunnerResult
    from argus_skill.planner.bounded_dag import BoundedDagNode, plan_bounded_dag

    older = memory.backlog.add(
        BacklogItem.new(title="older", objective="older", priority=100)
    )
    nested = memory.project_worktree / "nested" / "target"
    nested.mkdir(parents=True)
    decision = {
        "role": "planner",
        "payload": {
            "reason": "one typed nested task",
            "tasks": [{
                "key": "execute",
                "deps": [],
                "title": "`managed task`",
                "objective": "managed: operator request",
                "execution_workdir": "nested/target",
                "work_kind": "validation",
            }],
        },
    }

    class Runner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                agent_messages=[
                    f"ARGUS_ROLE_DECISION={json.dumps(decision)}"
                ],
            )

    plan = plan_bounded_dag(
        Runner(),
        "managed: operator request",
        workdir=memory.project_worktree,
    )
    assert plan.error == ""
    assert isinstance(plan.tasks[0], BoundedDagNode)
    monkeypatch.setattr(
        dispatch,
        "_plan_bounded_execution",
        lambda *args, **kwargs: plan,
    )

    item, alive, pid = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "memory"},
        root_task_id="root-task-1",
    )

    assert item.id == "root-task-1"
    assert item.title == "managed task"
    assert item.objective == "managed: operator request"
    assert item.work_kind == "validation"
    assert item.execution_workdir == str(nested.resolve())
    assert item.priority < older.priority
    assert (alive, pid) == (False, None)


def test_bounded_dispatch_fails_closed_without_planner_backend(memory) -> None:
    with pytest.raises(
        front_door.ManagerHandoffError,
        match="cannot preserve review and stage-transition semantics",
    ):
        dispatch._plan_bounded_execution(
            memory,
            "managed: review-only task",
            {"backend": "memory"},
        )


def test_manager_workdir_prefers_persisted_session_metadata(tmp_path) -> None:
    from argus_skill.core.session import SessionMeta, write_session_meta

    global_root = tmp_path / "root"
    wrong_worktree = tmp_path / "server-process-cwd"
    expected_worktree = tmp_path / "session-worktree"
    wrong_worktree.mkdir()
    expected_worktree.mkdir()
    sid = "s-manager-workdir"
    write_session_meta(
        global_root,
        SessionMeta(
            id=sid,
            cwd=str(expected_worktree),
            workdir=str(expected_worktree),
        ),
    )
    mem = MemoryBundle.for_cwd(
        wrong_worktree,
        global_root=global_root,
        fingerprint=sid,
    )

    assert dispatch._resolve_manager_workdir(mem) == expected_worktree.resolve()


def test_bounded_dispatch_uses_nested_node_worktree_as_its_only_contract_root(
    memory,
    monkeypatch,
):
    from argus_skill.core.campaign_workdir import adopt_campaign_workdir
    from argus_skill.core.pipeline_state import write_pipeline_state
    from argus_skill.skills.vertical_select import persist_vertical
    from argus_skill.verticals._data_domain import write_data_domain

    base = memory.project_worktree
    campaign = base / "campaign"
    target = campaign / "target"
    campaign.mkdir()
    subprocess.run(["git", "init", "-q", str(campaign)], check=True)
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    life_dir = front_door._life_dir_for(memory)
    adopt_campaign_workdir(
        state_root=life_dir,
        base_root=base,
        current_root=base,
        requested="campaign",
    )
    persist_vertical(campaign, "research", workflow_mode="staged")
    write_pipeline_state(
        campaign,
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
        purpose="target-only replay contract",
    )
    persist_vertical(target, "nested_replay", workflow_mode="staged")
    parent_artifact = campaign / "research" / "TARGET.md"
    parent_artifact.parent.mkdir(exist_ok=True)
    parent_artifact.write_text("stale parent evidence", encoding="utf-8")
    artifact = target / "research" / "TARGET.md"
    artifact.write_text("target evidence", encoding="utf-8")

    plan = SimpleNamespace(
        reason="replay the nested target",
        error="",
        tasks=(
            SimpleNamespace(
                key="target-node",
                deps=(),
                title="Run target replay",
                objective="Use only the nested target contract.",
                vertical="nested_replay",
                execution_workdir="target",
                context_refs=({
                    "kind": "artifact",
                    "ref": "research/TARGET.md",
                    "why": "target evidence",
                },),
            ),
        ),
    )
    monkeypatch.setattr(dispatch, "_plan_bounded_execution", lambda *args, **kwargs: plan)

    item, _, _ = dispatch.enqueue_mission(
        memory,
        "replay nested target",
        {"backend": "codex"},
    )

    assert item is not None
    assert item.execution_workdir == str(target.resolve())
    assert item.manager_decision["vertical"] == "nested_replay"
    assert "stage:target_scope" in item.tags
    assert "stage:submission" not in item.tags
    assert item.context_refs[0]["ref"] == "research/TARGET.md"
    assert item.context_refs[0]["content_hash"] == (
        "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    )
    assert adopt_campaign_workdir(
        state_root=life_dir,
        base_root=base,
        current_root=campaign,
        requested=item.execution_workdir,
    ) == target.resolve()


def test_bounded_dispatch_persists_real_dependency_dag(memory, monkeypatch):
    result = memory.project_worktree / "research" / "chem_playground" / "x" / "RESULT.md"
    result.parent.mkdir(parents=True)
    result.write_text("candidate result", encoding="utf-8")
    plan = SimpleNamespace(
        reason="fan out then integrate",
        error="",
        tasks=(
            SimpleNamespace(key="a", deps=(), title="Implement A", objective="write a.txt; test -s a.txt"),
            SimpleNamespace(key="b", deps=(), title="Implement B", objective="write b.txt; test -s b.txt"),
            SimpleNamespace(
                key="c",
                deps=("a", "b"),
                title="Integrate",
                objective="read a.txt and b.txt; write result.txt; test -s result.txt",
                hypothesis="The integrated route improves the measured outcome.",
                goal_contribution="Turn measured feedback into the requested result.",
                expected_regressions="One component may trade off against the other.",
                decision_rule="Revise the route when measured feedback misses the target.",
                acceptance_check="validator exits zero",
                non_goals=("do not edit pipeline state",),
                context_refs=({
                    "kind": "artifact",
                    "ref": "research/chem_playground/x/RESULT.md",
                    "why": "candidate result",
                    "content_hash": "",
                },),
                require_independent_review=True,
                skip_stage_transition=True,
            ),
        ),
    )
    monkeypatch.setattr(
        dispatch,
        "_plan_bounded_execution",
        lambda *args, **kwargs: plan,
    )

    first, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex"},
        root_task_id="root-task-dag",
    )

    items = {item.node_key: item for item in memory.backlog.all()}
    assert set(items) == {"a", "b", "c"}
    assert first.id == "root-task-dag"
    assert items["a"].deps == [] and items["b"].deps == []
    assert items["c"].deps == [items["a"].id, items["b"].id]
    assert {item.plan_id for item in items.values()} == {items["a"].plan_id}
    assert items["a"].plan_id.startswith("bounded-")
    assert all("bounded_dag_node" in item.tags for item in items.values())
    assert all(item.iterate for item in items.values())
    assert all(item.iteration_max_cycles == 3 for item in items.values())
    assert items["c"].plan_hypothesis.startswith("The integrated route")
    assert items["c"].decision_rule.startswith("Revise the route")
    assert all(item.original_objective == "managed: operator request" for item in items.values())
    assert items["c"].acceptance_check == "validator exits zero"
    assert items["c"].non_goals == ["do not edit pipeline state"]
    assert items["c"].context_refs[0]["ref"].endswith("/RESULT.md")
    assert items["c"].context_refs[0]["content_hash"].startswith("sha256:")
    assert "review:required" in items["c"].tags
    assert "stage_closing" not in items["c"].tags
    assert "stage_transition:skip" in items["c"].tags
    events = [
        json.loads(line)
        for line in (memory.project.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    verdict = next(event for event in events if event.get("type") == "life.planner.verdict")
    assert validate_event_envelope(verdict, require_known=True).valid
    assert verdict["status"] == "planned"
    assert verdict["success"] is True
    assert verdict["recoverable"] is False
    assert verdict["project_id"] == memory.project.root.name
    assert verdict["mission_id"] == items["a"].plan_id
    assert verdict["enqueued_tasks"] == 3


def test_bounded_dispatch_rejects_context_ref_outside_worktree(memory, monkeypatch):
    commit_calls = []

    class TrackingManager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(execution_task=f"managed: {body}")

        def commit_vertical_decision(self, body, decision, **kwargs):
            commit_calls.append((body, decision))
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=TrackingManager()),
    )
    plan = SimpleNamespace(
        reason="unsafe ref",
        error="",
        tasks=(
            SimpleNamespace(
                key="a",
                deps=(),
                title="Unsafe",
                objective="read outside worktree",
                context_refs=({
                    "kind": "artifact",
                    "ref": "../../outside.txt",
                    "why": "unsafe",
                    "content_hash": "",
                },),
            ),
        ),
    )
    monkeypatch.setattr(dispatch, "_plan_bounded_execution", lambda *args, **kwargs: plan)

    with pytest.raises(front_door.ManagerHandoffError, match="invalid context reference"):
        dispatch.enqueue_mission(
            memory,
            "operator request",
            {"backend": "codex"},
        )

    assert memory.backlog.all() == []
    assert commit_calls == []


def test_bounded_dispatch_merges_operator_attachment_context_refs(memory, monkeypatch):
    payload = memory.project_worktree / ".argus" / "attachments" / "s-dispatch01" / "att-deadbeefcafe" / "brief.md"
    payload.parent.mkdir(parents=True)
    payload.write_text("# brief\n", encoding="utf-8")
    plan = SimpleNamespace(
        reason="single task",
        error="",
        tasks=(
            SimpleNamespace(
                key="a",
                deps=(),
                title="Inspect upload",
                objective="Read the operator attachment.",
            ),
        ),
    )
    monkeypatch.setattr(dispatch, "_plan_bounded_execution", lambda *args, **kwargs: plan)

    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex"},
        context_refs=[{
            "kind": "attachment",
            "ref": ".argus/attachments/s-dispatch01/att-deadbeefcafe/brief.md",
            "why": "operator-uploaded attachment in the canonical project workdir",
            "attachment_id": "att-deadbeefcafe",
            "original_name": "brief.md",
            "mime": "text/markdown",
            "size_bytes": "8",
            "integrity": "01234567 89abcdef 01234567 89abcdef 01234567 89abcdef 01234567 89abcdef",
        }],
    )

    assert item is not None
    stored = memory.backlog.all()[0].context_refs[0]
    assert stored["kind"] == "attachment"
    assert stored["ref"].endswith("/brief.md")
    assert stored["original_name"] == "brief.md"
    assert stored["mime"] == "text/markdown"
    assert stored["size_bytes"] == "8"
    assert stored["integrity"].startswith("01234567")


def test_bounded_dispatch_rejects_context_revision_changed_before_commit(
    memory,
    monkeypatch,
):
    artifact = memory.project_worktree / "research" / "RESULT.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("planned revision", encoding="utf-8")
    commit_calls = []

    class MutatingLock:
        def __enter__(self):
            artifact.write_text("new revision", encoding="utf-8")

        def __exit__(self, exc_type, exc, traceback):
            return False

    class TrackingManager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(execution_task=f"managed: {body}")

        def pipeline_lock(self):
            return MutatingLock()

        def commit_vertical_decision(self, body, decision, **kwargs):
            commit_calls.append((body, decision))
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=TrackingManager()),
    )
    plan = SimpleNamespace(
        reason="stable context required",
        error="",
        tasks=(
            SimpleNamespace(
                key="a",
                deps=(),
                title="Read result",
                objective="Use the planned result revision.",
                context_refs=({
                    "kind": "artifact",
                    "ref": "research/RESULT.md",
                    "why": "planned result",
                    "content_hash": "",
                },),
            ),
        ),
    )
    monkeypatch.setattr(dispatch, "_plan_bounded_execution", lambda *args, **kwargs: plan)

    with pytest.raises(
        front_door.ManagerHandoffError,
        match="context references changed before Manager commit",
    ):
        dispatch.enqueue_mission(
            memory,
            "operator request",
            {"backend": "codex"},
        )

    assert commit_calls == []
    assert memory.backlog.all() == []


def test_continuous_dispatch_persists_operator_priority_item(memory):
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    payload = json.loads(
        (memory.project.root / "continuous.json").read_text(encoding="utf-8")
    )
    assert item is not None
    assert memory.backlog.all() == [item]
    assert item.title == "operator request"
    assert item.objective == "managed: operator request"
    assert item.original_objective == "managed: operator request"
    assert item.priority == -1
    assert "operator_priority" in item.tags
    assert "stage_transition:skip" in item.tags
    assert item.manager_decision == {"routed": True}
    assert payload["enabled"] is True
    assert payload["objective"] == "managed: operator request"
    assert payload["open_ended"] is True

    events = [
        json.loads(line)
        for line in (memory.project.root / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    queued = next(
        event
        for event in events
        if event.get("type") == "life.planner.task_added"
    )
    assert queued["item_id"] == item.id
    assert queued["source"] == "manager_operator"
    assert queued["operator_priority"] is True


def test_contextual_continuous_title_uses_manager_execution_task_in_every_status(
    memory,
    monkeypatch,
):
    from argus_skill.webapi.manager_session_intent import contextualize_operator_turn
    from argus_skill.webapi.project_state import compact_backlog_item

    execution_task = "修复上下文化连续任务的标题，并保留目标、依赖与状态行为。"
    routing_body = contextualize_operator_turn(
        "那就继续修最后一个问题",
        [
            {"role": "operator", "text": "先处理标题泄露问题。"},
            {"role": "argus", "text": "会保持普通研究任务行为不变。"},
        ],
        last_team_task="分阶段修复 Argus 用户体验问题。",
    )

    class ContextResolvingManager:
        def decide_vertical(self, body, **kwargs):
            assert body == routing_body
            return SimpleNamespace(
                execution_task=execution_task,
                workflow_mode="staged",
            )

        def commit_vertical_decision(self, body, decision, **kwargs):
            assert body == routing_body
            return decision

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=ContextResolvingManager()),
    )

    item, _, _ = dispatch.enqueue_mission(
        memory,
        routing_body,
        {"backend": "codex", "config": {"continuous": True}},
    )

    assert item is not None
    assert item.objective == execution_task
    assert item.original_objective == execution_task
    assert item.deps == []
    views = [compact_backlog_item(item)]
    running = memory.backlog.mark_running(item.id)
    assert running is not None
    views.append(compact_backlog_item(running))
    failed = memory.backlog.mark_failed(item.id, error="受控失败")
    assert failed is not None
    views.append(compact_backlog_item(failed))

    assert [view["status"] for view in views] == ["pending", "running", "failed"]
    assert {view["title"] for view in views} == {execution_task}
    for view in views:
        visible = json.dumps(view, ensure_ascii=False)
        assert "[BOUNDED TASK CONTEXT" not in visible
        assert "[CURRENT OPERATOR MESSAGE]" not in visible
        assert "先处理标题泄露问题" not in visible


def test_continuous_replacement_queues_operator_task_after_running_work(memory):
    from argus_skill.daemon.state import (
        read_continuous_state,
        write_continuous_config,
    )

    write_continuous_config(
        memory.project.root,
        enabled=True,
        objective="old campaign",
    )
    running = memory.backlog.add(
        BacklogItem.new(
            title="current mission",
            objective="finish current safe increment",
            priority=10,
            manager_decision={"routed": True},
        )
    )
    memory.backlog.mark_running(running.id)
    stale = memory.backlog.add(
        BacklogItem.new(
            title="autonomous cleanup",
            objective="reconcile an optional manifest",
            priority=20,
            manager_decision={"routed": True},
        )
    )

    queued, _, _ = dispatch.enqueue_mission(
        memory,
        "download and quantize the BF16 model",
        {"backend": "codex", "config": {"continuous": True}},
    )

    rows = {item.id: item for item in memory.backlog.all()}
    assert rows[running.id].status == "running"
    assert rows[stale.id].status == "superseded"
    assert queued is not None
    assert rows[queued.id].status == "pending"
    assert memory.backlog.next_pending().id == queued.id
    assert read_continuous_state(memory.project.root).objective == (
        "managed: download and quantize the BF16 model"
    )


def test_lifetime_promotion_sets_pending_handoff(memory):
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    assert dispatch.maybe_promote_to_continuous(memory, "keep researching", state)
    assert state["config"]["continuous"] is True
    assert state["_continuous_pending_manager_handoff"] is True
    assert state["_continuous_open_ended"] is True
    assert state["continuous_objective"] == ""


def test_lifetime_promotion_revalidates_existing_continuous_state(
    memory, monkeypatch,
):
    from argus_skill.daemon.state import write_continuous_config

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    write_continuous_config(
        memory.project.root,
        enabled=True,
        objective="existing campaign",
    )
    state = {
        "backend": "codex",
        "config": {"continuous": False},
        "_frontdoor_lifetime": "standing",
    }

    assert dispatch.maybe_promote_to_continuous(memory, "keep researching", state)
    assert state["config"]["continuous"] is True
    assert state["continuous_objective"] == "existing campaign"
    assert "_continuous_pending_manager_handoff" not in state


def test_lifetime_promotion_repairs_stale_continuous_cache(memory, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    state = {
        "backend": "codex",
        "config": {"continuous": True},
        "_frontdoor_lifetime": "standing",
    }

    assert dispatch.maybe_promote_to_continuous(memory, "new campaign", state)
    assert state["_continuous_pending_manager_handoff"] is True
    assert state["continuous_objective"] == ""


def test_lifetime_promotion_keeps_explicit_bounded_direct_task_finite(memory):
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert not dispatch.maybe_promote_to_continuous(
        memory,
        "one report",
        state,
        workflow_mode="direct",
    )
    assert state["config"]["continuous"] is False
    assert "_frontdoor_lifetime" not in state


def test_missing_lifetime_defaults_direct_task_to_bounded(memory):
    state = {"backend": "codex"}

    assert not dispatch.maybe_promote_to_continuous(
        memory,
        "one report",
        state,
        workflow_mode="direct",
    )
    assert state["config"]["continuous"] is False


def test_finite_staged_task_uses_durable_campaign_supervisor(memory, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert dispatch.maybe_promote_to_continuous(
        memory,
        "给我写个论文 iclr的 我要投稿",
        state,
        workflow_mode="staged",
    )
    assert state["config"]["continuous"] is True
    assert state["_continuous_pending_manager_handoff"] is True
    assert state["_continuous_open_ended"] is False
    assert "_frontdoor_lifetime" not in state


def test_explicit_bounded_increment_overrides_normally_staged_workflow(memory):
    state = {
        "backend": "codex",
        "_frontdoor_lifetime": "bounded_increment",
    }

    assert not dispatch.maybe_promote_to_continuous(
        memory,
        "只完成 research 阶段，不要进入 plan",
        state,
        workflow_mode="staged",
    )
    assert state["config"]["continuous"] is False
    assert "_frontdoor_lifetime" not in state


def test_lifetime_promotion_validates_the_life_backend(memory, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "0")
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    with pytest.raises(
        front_door.ManagerHandoffError,
        match="ARGUS_SKILL_LIFE_BACKEND=memory cannot plan",
    ):
        dispatch.maybe_promote_to_continuous(memory, "one report", state)

    assert "config" not in state


def test_lifetime_promotion_validates_the_active_daemon_backend(
    memory, monkeypatch,
):
    from argus_skill.daemon import life_worker

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "0")
    monkeypatch.setattr(
        life_worker,
        "read_daemon_status",
        lambda life_dir: SimpleNamespace(
            alive=True,
            backend="copilot",
            life_backend="memory",
        ),
    )
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    with pytest.raises(
        front_door.ManagerHandoffError,
        match="ARGUS_SKILL_LIFE_BACKEND=memory cannot plan",
    ):
        dispatch.maybe_promote_to_continuous(memory, "one report", state)

    assert "config" not in state


def test_team_bounded_verdict_selects_bounded_dispatch(memory):
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert not dispatch.maybe_promote_to_continuous(memory, "one report", state)
    assert state["config"]["continuous"] is False
    assert memory.backlog.all() == []


def test_lifetime_promotion_never_calls_a_second_classifier(memory, monkeypatch):
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("front-door lifetime must avoid a second model call")
        ),
    )
    standing = {"backend": "codex", "_frontdoor_lifetime": "standing"}
    bounded = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert dispatch.maybe_promote_to_continuous(memory, "keep going", standing)
    assert not dispatch.maybe_promote_to_continuous(memory, "one report", bounded)
    assert "_frontdoor_lifetime" not in standing
    assert "_frontdoor_lifetime" not in bounded


def test_failed_continuous_handoff_rolls_back_auto_promotion(memory, monkeypatch):
    state = {
        "backend": "codex",
        "config": {"continuous": True},
        "_continuous_pending_manager_handoff": True,
    }
    monkeypatch.setattr(
        front_door,
        "manager_continuous_handoff",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    with pytest.raises(RuntimeError, match="failed"):
        dispatch.enqueue_mission(memory, "keep researching", state)

    assert state["config"]["continuous"] is False
    assert state["continuous_objective"] == ""


def test_the_operator_item_does_not_claim_planner_authorship(memory):
    """``preplanned`` is computed as ``"planner" in item.tags``, and it skips
    the advisory planning pass on the ground that a Planner already decomposed
    the work. A raw operator message has not been decomposed by anyone, so the
    tag sent it straight to a single Engineer — the opposite of the chain
    ``_maybe_draft_plan`` documents for user-authored bounded work.
    """
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    assert item is not None
    assert "planner" not in item.tags
    # Still the operator's priority work, still bounded: only the authorship
    # claim is dropped.
    assert "operator_priority" in item.tags
    assert "scope:bounded" in item.tags


def test_the_operator_item_is_not_treated_as_preplanned(memory):
    """Asserted through the reader rather than the tag, so this keeps holding
    if the discriminator moves."""
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    preplanned = any(
        str(tag).strip().lower() == "planner" for tag in getattr(item, "tags", [])
    )

    assert preplanned is False


def test_the_operator_item_satisfies_the_review_only_contract(memory):
    """``_prepare_persist`` rejects a Planner node that skips the stage
    transition without requiring independent review. The Manager's own operator
    item set exactly that pair, exempting itself from the contract it enforces.
    """
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    assert item is not None
    assert "stage_transition:skip" in item.tags
    assert "review:required" in item.tags


def test_the_operator_mission_gets_an_independent_reviewer(memory):
    """Read through the same helper the mission runtime uses. Without the tag,
    ``round_self_review`` settles the mission on the Engineer's own
    MILESTONE_STATUS=DONE and no Reviewer ever runs.
    """
    from argus_skill.life.supervisor._planning_context import PlanningContextMixin

    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    assert PlanningContextMixin._item_requires_independent_review(item) is True
    assert PlanningContextMixin._item_skips_stage_transition(item) is True


def test_the_operator_item_keeps_stage_authority_with_the_manager(memory):
    """The two tags together are what makes the skip real: the runtime honors
    ``skip_stage_transition`` only alongside ``require_independent_review`` on a
    bounded scope, and otherwise falls through to the self-review arm and moves
    the stage anyway."""
    from argus_skill.apps._runtime_helpers import _should_run_stage_transition

    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )
    assert item is not None

    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        skip_stage_transition=True,
        require_independent_review=True,
        review_source="reviewer",
    ) is False
    # What the untagged item actually did: the skip is not honored on its own,
    # so the self-review arm moved the stage the tag exists to hold still.
    assert _should_run_stage_transition(
        "done",
        mission_scope="bounded",
        skip_stage_transition=True,
        require_independent_review=False,
        review_source="engineer_self_review",
    ) is True
