"""backlog.jsonl is writable, so the Manager must notice when it is bypassed.

An outer agent can append a row to backlog.jsonl and the daemon will execute
it. Nothing errors — but the item skipped manager_bounded_handoff, so no
vertical was chosen, no stage or target level was set, and the run proceeds
under the default workflow. The symptom is a Manager that appears idle while
a literature-review task runs as a bare objective.

File permissions cannot close this and should not: writing to the backlog is
a reasonable thing to want. So the item is marked, noticed, and re-routed.

These tests also pin the three wiring points, because the guard module once
shipped with all three missing and became dead code that nothing called.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from argus.life.memory import BacklogItem
from argus.life.supervisor.backlog_guard import (
    DECISION_KEY,
    ensure_manager_decision,
    needs_manager_decision,
)
from tests.hand_off import manager_runner


def _written_directly(**kw) -> BacklogItem:
    """An item as an outer agent would append it: no Manager decision."""
    return BacklogItem.new(title=kw.pop("title", "read the literature"),
                           objective=kw.pop("objective", "read the literature"), **kw)


# -- detection --------------------------------------------------------------


# -- what the operator is told ---------------------------------------------


# -- evidence ---------------------------------------------------------------


def test_guard_reuses_the_daemon_manager_instead_of_building_a_runner(
    tmp_path,
    monkeypatch,
) -> None:
    from argus.life import MemoryBundle
    from argus.manager import front_door

    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-reuse-daemon-manager",
    )
    mem.init()
    item = mem.backlog.add(_written_directly(objective="profile the hot path"))
    calls: list[tuple[str, str | None]] = []

    class Manager:
        def decide_vertical(self, body: str, *, root_task_id: str | None = None):
            calls.append((body, root_task_id))
            return SimpleNamespace(
                assumptions=(),
                execution_task=f"managed: {body}",
                vertical="software",
                stage="implementation",
                workflow_mode="staged",
            )

    def fail_if_built(*_args, **_kwargs):
        raise AssertionError("the guard must reuse the daemon's Manager")

    monkeypatch.setattr(front_door, "_ensure_manager_runner", fail_if_built)

    routed = ensure_manager_decision(mem, item, manager=Manager())

    assert calls == [("profile the hot path", item.id)]
    assert routed.objective == "managed: profile the hot path"
    assert routed.manager_decision == {
        "vertical": "software",
        "stage": "implementation",
        "workflow_mode": "staged",
        "require_independent_review": True,
        "routed": True,
    }
    assert needs_manager_decision(routed) is False


def test_guard_reroutes_unknown_persisted_vertical(tmp_path, monkeypatch) -> None:
    from argus.life.memory import LifeMemory
    from argus.manager import front_door

    memory = LifeMemory.open(tmp_path)
    item = memory.backlog.add(
        BacklogItem.new(
            title="Run portable MLX baseline",
            objective="Build the MLX quantization baseline.",
            manager_decision={
                "routed": True,
                "vertical": "mlx_model_optimization",
            },
        )
    )

    def fake_prepare(mem, objective, state, **kwargs):
        del mem, state, kwargs
        assert objective == "Build the MLX quantization baseline."
        return SimpleNamespace(
            execution_task=objective,
            decision=SimpleNamespace(
                vertical="software",
                workflow_mode="direct",
            ),
        )

    monkeypatch.setattr(front_door, "prepare_manager_execution_task", fake_prepare)

    routed = ensure_manager_decision(memory, item)

    assert routed.manager_decision == {
        "vertical": "software",
        "workflow_mode": "direct",
        "require_independent_review": True,
        "routed": True,
    }


# -- the wiring, which once went missing -----------------------------------

def test_the_backlog_item_carries_the_field() -> None:
    assert DECISION_KEY in {f.name for f in BacklogItem.__dataclass_fields__.values()}


def test_the_dispatch_helper_can_record_a_decision() -> None:
    from argus.apps import _life_actions

    signature = inspect.signature(_life_actions.add_backlog_item)

    assert "manager_decision" in signature.parameters


def test_the_supervisor_routes_before_executing() -> None:
    from argus.life.supervisor import _mission_execution

    source = inspect.getsource(_mission_execution)
    claim_at = source.index("claim_next(")
    guard_at = source.index("ensure_manager_decision(")
    context_at = source.index("_prepare_mission_context(")

    # Must happen after the claim and before the mission context is built,
    # or the run proceeds under the default workflow.
    assert claim_at < guard_at < context_at
    assert "_bound_manager()" in source
    assert "manager=manager" in source


def test_status_reports_bypassed_items() -> None:
    from argus.apps.cli import _core

    source = inspect.getsource(_core)

    # Nothing errors when the Manager is bypassed, so --status has to say it
    # or the blindness stays invisible.
    assert "describe_undecided" in source


def test_dispatch_records_manager_decision_on_the_operator_item(
    tmp_path,
    monkeypatch,
) -> None:
    from argus.life import MemoryBundle
    from argus.manager import dispatch, front_door

    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-backlog-guard",
    )
    mem.init()

    class Manager:
        def decide_vertical(self, body: str, **_kwargs):
            return SimpleNamespace(
                assumptions=(),
                execution_task=f"managed: {body}",
                vertical="argus_maintenance",
                stage="scope",
                workflow_mode="bounded",
            )

        def commit_vertical_decision(self, _body: str, decision, **_kwargs):
            return SimpleNamespace(
                execution_task=decision.execution_task,
                vertical=decision.vertical,
                stage=decision.stage,
                workflow_mode=decision.workflow_mode,
            )

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda _state, _mem: manager_runner(Manager()),
    )

    dispatch.enqueue_mission(
        mem,
        "operator-approved maintenance increment",
        {"backend": "memory"},
        root_task_id="root-manager-decision",
    )

    (item,) = mem.backlog.all()
    assert item.id == "root-manager-decision"
    assert item.manager_decision.get("routed") is True
    assert item.manager_decision.get("workflow_mode") == "bounded"
    assert needs_manager_decision(item) is False
