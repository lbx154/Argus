from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.life import BacklogItem, MemoryBundle
from argus.manager import dispatch, front_door
from tests.hand_off import manager_runner


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
def _offline_manager_runner(monkeypatch):
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(assumptions=(), execution_task=f"managed: {body}")

        def commit_vertical_decision(self, body, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: manager_runner(Manager()),
    )


def _direct_manager(monkeypatch, **decision) -> None:
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(**{
                "assumptions": (),
                "execution_task": f"managed: {body}",
                "vertical": "software",
                "workflow_mode": "direct",
                "require_independent_review": True,
                **decision,
            })

        def commit_vertical_decision(self, body, decision, **kwargs):
            return decision

        def plan_stages(self, vertical):
            return []

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *_args, **_kwargs: manager_runner(Manager()),
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
    state = {"backend": "codex", "_team_lifetime": "bounded"}

    item, _, _ = dispatch.enqueue_mission(memory, "one finite check", state)

    assert "manager_direct" in item.tags
    campaign = read_continuous_state(memory.project.root)
    assert campaign.enabled is True
    assert campaign.objective == "existing campaign"
    assert state["continuous_objective"] == "existing campaign"
    assert "_team_lifetime" not in state


def test_the_readings_the_route_chose_are_recorded_for_its_task(memory, monkeypatch) -> None:
    from argus.core.autonomy import read_autonomous_assumptions

    reading = {"chose": "round half up", "instead_of": "truncate like format_kib"}
    _direct_manager(monkeypatch, assumptions=(reading,), form="route")

    item, _, _ = dispatch.enqueue_mission(memory, "round the minutes", {})

    rows = read_autonomous_assumptions(memory.project.root)
    assert [(row["item_id"], row["reading"], row["conflict"], row["source"]) for row in rows] == [
        (item.id, "round half up", "truncate like format_kib", "route"),
    ]


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


def test_only_a_ticket_that_names_a_required_step_is_tied_to_it(memory, monkeypatch) -> None:
    from argus.manager.domain_author import RouteContext
    from argus.webapi import canvas as store

    life = memory.project.root
    _c, first = store.add_card(life, 0, kind="step", text="写核心函数")
    _c, second = store.add_card(life, 1, kind="step", text="加命令行")
    store.add_link(life, 2, source=first["id"], target=second["id"], kind="order")

    # Work the Manager did not tie to a step stays the operator's own request.
    _direct_manager(monkeypatch)
    item, _, _ = dispatch.enqueue_mission(memory, "把 README 里的安装命令修一下", {})
    assert item.waypoint_id == "" and "[REQUIRED STEP]" not in item.objective
    assert "stage_closing" in item.tags

    _direct_manager(monkeypatch, waypoint_id=first["id"])
    item, _, _ = dispatch.enqueue_mission(memory, "做 wordstat", {})
    assert item.waypoint_id == first["id"]
    assert "carries out only the operator's step 1" in item.objective
    assert "«写核心函数»" in item.acceptance_check and "stage_closing" not in item.tags

    with pytest.raises(ValueError, match="waypoint_id: name one of the operator's required steps"):
        RouteContext(project_root=life)._waypoint({"waypoint_id": "hc-nope"})
    assert RouteContext(project_root=life)._waypoint({"waypoint_id": second["id"]}) == second["id"]
