from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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


def test_bounded_dispatch_persists_manager_handoff_and_root_id(memory):
    older = memory.backlog.add(
        BacklogItem.new(title="older", objective="older", priority=100)
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


def test_lifetime_promotion_sets_pending_handoff(memory, monkeypatch):
    runner = SimpleNamespace(classify_needs_continuous=lambda body: True)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda state, mem: runner)
    state = {"backend": "codex"}

    assert dispatch.maybe_promote_to_continuous(memory, "keep researching", state)
    assert state["config"]["continuous"] is True
    assert state["_continuous_pending_manager_handoff"] is True
    assert state["continuous_objective"] == ""


def test_lifetime_promotion_keeps_explicit_bounded_task(memory, monkeypatch):
    runner = SimpleNamespace(classify_needs_continuous=lambda body: False)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda state, mem: runner)
    state = {"backend": "codex"}

    assert not dispatch.maybe_promote_to_continuous(memory, "one report", state)
    assert "config" not in state


def test_lifetime_promotion_reuses_frontdoor_verdict_without_second_call(
    memory, monkeypatch,
):
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
