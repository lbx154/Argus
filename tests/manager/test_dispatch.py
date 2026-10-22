from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from argus.life import BacklogItem, MemoryBundle
from argus.manager import dispatch, front_door


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


def _direct_manager(monkeypatch, **decision) -> None:
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(
                execution_task=f"managed: {body}",
                vertical="software",
                workflow_mode="direct",
                require_independent_review=True,
                **decision,
            )

        def commit_vertical_decision(self, body, decision, **kwargs):
            return decision

        def plan_stages(self, vertical):
            return []

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(manager=Manager()),
    )


def _events(memory) -> list[dict]:
    return [
        json.loads(line)
        for line in (memory.project.root / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_direct_task_arms_a_finite_campaign_with_one_reviewed_mission(
    memory,
    monkeypatch,
) -> None:
    from argus.daemon.state import read_continuous_state

    _direct_manager(monkeypatch)
    state = {"backend": "codex"}

    item, alive, pid = dispatch.enqueue_mission(
        memory,
        "one coherent package",
        state,
        root_task_id="root-direct-1",
        context_refs=[{
            "kind": "attachment",
            "ref": "brief.md",
            "why": "operator input",
        }],
    )

    assert item.id == "root-direct-1"
    assert item.objective == "managed: one coherent package"
    assert item.original_objective == item.objective
    assert item.iterate is False
    assert item.iteration_max_cycles == 1
    assert item.deps == []
    assert item.context_refs == [{
        "kind": "attachment",
        "ref": "brief.md",
        "why": "operator input",
    }]
    assert {"manager_direct", "stage_closing", "review:required"} <= set(item.tags)
    assert "planner" not in item.tags
    assert "stage_transition:skip" not in item.tags
    # The roles read the Manager's brief; the operator reads their own request.
    assert item.manager_decision == {
        "vertical": "software",
        "workflow_mode": "direct",
        "require_independent_review": True,
        "routed": True,
        "display_objective": "one coherent package",
        "route_source": "manager",
    }
    assert (alive, pid) == (False, None)
    campaign = read_continuous_state(memory.project.root)
    assert campaign.enabled and not campaign.open_ended
    assert campaign.objective == "managed: one coherent package"
    assert state["config"]["continuous"] is True

    events = _events(memory)
    queued = next(
        event
        for event in events
        if event.get("type") == "life.planner.task_added"
    )
    assert queued["source"] == "manager_direct"
    assert not any(
        event.get("type") == "life.planner.verdict"
        and event.get("status") == "planned"
        for event in events
    )


def test_direct_task_joins_a_running_campaign_without_replacing_it(
    memory,
    monkeypatch,
) -> None:
    from argus.daemon.state import read_continuous_state, write_continuous_config

    write_continuous_config(
        memory.project.root, enabled=True, objective="existing campaign",
    )
    _direct_manager(monkeypatch)
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    item, _, _ = dispatch.enqueue_mission(memory, "one finite check", state)

    assert "manager_direct" in item.tags
    campaign = read_continuous_state(memory.project.root)
    assert campaign.enabled is True
    assert campaign.objective == "existing campaign"
    assert state["continuous_objective"] == "existing campaign"
    assert "_frontdoor_lifetime" not in state


def test_a_choice_the_operator_reserved_waits_on_them_instead_of_dispatching(
    memory,
    monkeypatch,
) -> None:
    """2026-10-20: the Manager read that only the operator could pick A or B,
    yet an Engineer spent 44 seconds finding that out. The task now waits on
    the Manager's question and resumes on the answer."""
    from argus.life.memory import park_reason

    _direct_manager(monkeypatch, operator_question="A or B?", adaptation_reason="A and B conflict")

    item, _, _ = dispatch.enqueue_mission(memory, "pick A or B, then change truncate", {})

    stored = next(row for row in memory.backlog.active() if row.id == item.id)
    assert park_reason(stored) == "operator_question"
    assert stored.pending_question == stored.operator_decision["question"] == "A or B?"
    assert memory.backlog.next_pending() is None
    assert any(
        event.get("type") == "life.operator_question.pending" and event["item_id"] == item.id
        for event in _events(memory)
    )

    resumed, _ = memory.backlog.resume_with_operator_reply(item.id, "A")
    assert memory.backlog.next_pending().id == resumed.id == item.id
    assert "manager_direct" in resumed.tags


@pytest.mark.parametrize(
    ("lifetime", "open_ended"),
    [(None, False), ("bounded", False), ("garbage", False), ("standing", True)],
)
def test_every_team_task_arms_a_planner_run_campaign(memory, lifetime, open_ended):
    from argus.daemon.state import read_continuous_state

    state = {"backend": "codex"}
    if lifetime is not None:
        state["_frontdoor_lifetime"] = lifetime

    item, _, _ = dispatch.enqueue_mission(memory, "write the paper", state)

    campaign = read_continuous_state(memory.project.root)
    assert campaign.enabled is True
    assert campaign.open_ended is open_ended
    assert campaign.objective == "managed: write the paper"
    assert "operator_priority" in item.tags
    assert "_frontdoor_lifetime" not in state


def test_direct_item_carries_the_stage_the_supervisor_replays(memory, monkeypatch):
    """The item is bound to the stage kept in the project state root.

    The Manager records the campaign stage under the project state root while
    the team works in a separate workspace with no pipeline state. Tagging the
    item from the workspace gave it the vertical's first stage, and the
    supervisor never replayed its reviewed result for the real stage.
    """
    from argus.core.pipeline_state import write_pipeline_state
    from argus.life.supervisor._planning_cycle_enqueue import (
        PlanningCycleEnqueueMixin,
    )
    from argus.skills.stage_machine import current_stage

    state_root = front_door._life_dir_for(memory)
    workspace = memory.project_worktree
    assert state_root != workspace
    write_pipeline_state(
        state_root,
        {"vertical": "software", "workflow_mode": "direct", "current_stage": "delivery"},
    )
    assert current_stage(workspace) != "delivery"
    _direct_manager(monkeypatch)

    item, _, _ = dispatch.enqueue_mission(memory, "write greet.py", {"backend": "codex"})

    assert PlanningCycleEnqueueMixin._item_pipeline_stage(item) == "delivery"


def test_resumed_campaign_workdir_takes_the_state_root_stage(memory, monkeypatch):
    """After a campaign adopted its own workdir, the item follows the state root."""
    from argus.core.campaign_workdir import adopt_campaign_workdir
    from argus.core.pipeline_state import write_pipeline_state
    from argus.life.supervisor._planning_cycle_enqueue import PlanningCycleEnqueueMixin

    state_root = front_door._life_dir_for(memory)
    base = memory.project_worktree
    campaign = base / "campaign"
    campaign.mkdir()
    subprocess.run(["git", "init", "-q", str(campaign)], check=True)
    adopt_campaign_workdir(
        state_root=state_root, base_root=base, current_root=base, requested="campaign",
    )
    write_pipeline_state(
        state_root,
        {"vertical": "research", "workflow_mode": "direct", "current_stage": "paper"},
    )
    _direct_manager(monkeypatch)

    item, _, _ = dispatch.enqueue_mission(memory, "resume the campaign", {"backend": "codex"})

    assert dispatch._resolve_manager_workdir(memory) == campaign.resolve()
    assert PlanningCycleEnqueueMixin._item_pipeline_stage(item) == "paper"


def test_manager_workdir_prefers_persisted_session_metadata(tmp_path) -> None:
    from argus.core.session import SessionMeta, write_session_meta

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


def test_dispatch_merges_operator_attachment_context_refs(memory):
    payload = memory.project_worktree / ".argus" / "attachments" / "s-dispatch01" / "att-deadbeefcafe" / "brief.md"
    payload.parent.mkdir(parents=True)
    payload.write_text("# brief\n", encoding="utf-8")
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


def test_continuous_dispatch_persists_operator_priority_item(memory):
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "_frontdoor_lifetime": "standing"},
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
    assert item.manager_decision == {
        "require_independent_review": True,
        "routed": True,
        "display_objective": "operator request",
    }
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
    from argus.webapi.manager_session_intent import contextualize_operator_turn
    from argus.webapi.project_state import compact_backlog_item

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
    # The title is the task's short name: its clause, not the sentence's period.
    assert {view["title"] for view in views} == {execution_task.rstrip("。")}
    for view in views:
        visible = json.dumps(view, ensure_ascii=False)
        assert "[BOUNDED TASK CONTEXT" not in visible
        assert "[CURRENT OPERATOR MESSAGE]" not in visible
        assert "先处理标题泄露问题" not in visible


def test_continuous_replacement_queues_operator_task_after_running_work(memory):
    from argus.daemon.state import (
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


def test_dispatch_validates_the_life_backend(memory, monkeypatch):
    from argus.daemon.state import read_continuous_state

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "0")
    state = {"backend": "codex"}

    with pytest.raises(
        front_door.ManagerHandoffError,
        match="ARGUS_SKILL_LIFE_BACKEND=memory cannot plan",
    ):
        dispatch.enqueue_mission(memory, "one report", state)

    assert memory.backlog.all() == []
    assert not read_continuous_state(memory.project.root).enabled
    assert "config" not in state


def test_dispatch_validates_the_active_daemon_backend(memory, monkeypatch):
    from argus.daemon import life_worker

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

    with pytest.raises(
        front_door.ManagerHandoffError,
        match="ARGUS_SKILL_LIFE_BACKEND=memory cannot plan",
    ):
        dispatch.enqueue_mission(memory, "one report", {"backend": "codex"})

    assert memory.backlog.all() == []


def test_failed_campaign_handoff_publishes_nothing(memory, monkeypatch):
    state = {"backend": "codex"}
    monkeypatch.setattr(
        front_door,
        "manager_continuous_handoff",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    with pytest.raises(RuntimeError, match="failed"):
        dispatch.enqueue_mission(memory, "keep researching", state)

    assert "config" not in state
    assert memory.backlog.all() == []


def test_the_operator_item_does_not_claim_planner_authorship(memory):
    """``preplanned`` is computed as ``"planner" in item.tags`` and marks work a
    Planner already decomposed. Nobody decomposed a raw operator message."""
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    assert "planner" not in item.tags
    assert "operator_priority" in item.tags
    assert "scope:bounded" in item.tags


def test_the_operator_item_satisfies_the_review_only_contract(memory):
    """Skipping the stage transition is honored only alongside required
    independent review; the operator item must carry the pair together."""
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
    ``round_routing`` settles the mission on the Engineer's own
    MILESTONE_STATUS=DONE and no Reviewer ever runs.
    """
    from argus.life.supervisor._planning_context import PlanningContextMixin

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
    from argus.apps._runtime_helpers import _should_run_stage_transition

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
