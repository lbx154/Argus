from __future__ import annotations

import json
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


def test_bounded_dispatch_persists_manager_handoff_and_root_id(memory, monkeypatch):
    older = memory.backlog.add(
        BacklogItem.new(title="older", objective="older", priority=100)
    )
    monkeypatch.setattr(
        dispatch,
        "_plan_bounded_execution",
        lambda *args, **kwargs: SimpleNamespace(
            reason="one atomic task",
            error="",
            tasks=(
                SimpleNamespace(
                    key="execute",
                    deps=(),
                    title="managed task",
                    objective="managed: operator request",
                ),
            ),
        ),
    )

    item, alive, pid = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "memory"},
        root_task_id="root-task-1",
    )

    assert item.id == "root-task-1"
    assert item.objective == "managed: operator request"
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
    assert all(item.iterate is False for item in items.values())
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


def test_continuous_dispatch_persists_only_manager_handoff(memory):
    item, _, _ = dispatch.enqueue_mission(
        memory,
        "operator request",
        {"backend": "codex", "config": {"continuous": True}},
    )

    payload = json.loads(
        (memory.project.root / "continuous.json").read_text(encoding="utf-8")
    )
    assert item is None
    assert memory.backlog.all() == []
    assert payload["enabled"] is True
    assert payload["objective"] == "managed: operator request"


def test_lifetime_promotion_sets_pending_handoff(memory):
    state = {"backend": "codex"}

    assert dispatch.maybe_promote_to_continuous(memory, "keep researching", state)
    assert state["config"]["continuous"] is True
    assert state["_continuous_pending_manager_handoff"] is True
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
    assert "_frontdoor_lifetime" not in state


def test_lifetime_promotion_validates_the_life_backend(memory, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "0")
    state = {"backend": "codex"}

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
    state = {"backend": "codex"}

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
