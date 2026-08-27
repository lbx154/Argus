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

from argus_skill.life.memory import BacklogItem
from argus_skill.life.supervisor.backlog_guard import (
    DECISION_KEY,
    decision_evidence,
    describe_undecided,
    ensure_manager_decision,
    needs_manager_decision,
    undecided_items,
)


def _written_directly(**kw) -> BacklogItem:
    """An item as an outer agent would append it: no Manager decision."""
    return BacklogItem.new(title=kw.pop("title", "read the literature"),
                           objective=kw.pop("objective", "read the literature"), **kw)


def _routed(**kw) -> BacklogItem:
    return BacklogItem.new(
        title="dispatched work",
        objective="dispatched work",
        manager_decision={"routed": True, "vertical": "research"},
        **kw,
    )


# -- detection --------------------------------------------------------------

def test_a_directly_written_item_is_detected() -> None:
    assert needs_manager_decision(_written_directly()) is True


def test_a_dispatched_item_is_left_alone() -> None:
    assert needs_manager_decision(_routed()) is False


def test_manager_route_survives_backlog_serialization() -> None:
    restored = BacklogItem.from_jsonable(_routed().to_jsonable())

    assert restored.manager_decision == {
        "routed": True,
        "vertical": "research",
    }
    assert needs_manager_decision(restored) is False


def test_a_decision_without_the_routed_flag_does_not_count() -> None:
    # Half-written metadata must not read as a routing.
    item = BacklogItem.new(title="t", objective="o", manager_decision={"vertical": "math"})

    assert needs_manager_decision(item) is True


def test_items_predating_the_field_are_treated_as_unrouted() -> None:
    # Re-routing an old item costs one Manager call; assuming it was routed
    # preserves exactly the blindness this exists to remove.
    item = BacklogItem.new(title="t", objective="o")
    item.manager_decision = {}

    assert needs_manager_decision(item) is True


def test_only_pending_items_are_reported() -> None:
    # An item already running or finished cannot be re-routed usefully; the
    # guard is about what is about to execute.
    running = _written_directly()
    running.status = "running"
    done = _written_directly()
    done.status = "done"
    waiting = _written_directly(title="still queued")

    reported = undecided_items([running, done, waiting])

    assert [item.title for item in reported] == ["still queued"]


# -- what the operator is told ---------------------------------------------

def test_the_summary_names_the_items_and_the_cause() -> None:
    text = describe_undecided([_written_directly(title="survey the literature")])

    assert "without a Manager decision" in text
    assert "survey the literature" in text
    # The cause matters more than the count: it explains the idle Manager.
    assert "no vertical, stage, or target level was chosen" in text


def test_nothing_is_said_when_everything_was_dispatched() -> None:
    assert describe_undecided([_routed(), _routed()]) == ""


def test_the_summary_stays_short_for_many_items() -> None:
    text = describe_undecided([_written_directly() for _ in range(9)])

    assert "9 backlog item(s)" in text
    assert "+6 more" in text


# -- evidence ---------------------------------------------------------------

def test_decision_evidence_keeps_the_routing_facts() -> None:
    class _Decision:
        vertical = "research"
        stage = "run"
        workflow_mode = "bounded"
        research_target_level = "publishable"
        require_independent_review = True

    evidence = decision_evidence(_Decision())

    assert evidence["routed"] is True
    assert evidence["vertical"] == "research"
    assert evidence["require_independent_review"] is True
    assert evidence["research_target_level"] == "publishable"


def test_an_empty_decision_yields_no_false_routing_mark() -> None:
    assert decision_evidence(None) == {}


def test_guard_reuses_the_daemon_manager_instead_of_building_a_runner(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.life import MemoryBundle
    from argus_skill.manager import front_door

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


def test_guard_uses_injected_supervisor_runner(tmp_path, monkeypatch) -> None:
    from argus_skill.life.memory import LifeMemory
    from argus_skill.manager import front_door

    memory = LifeMemory.open(tmp_path)
    item = memory.backlog.add(_written_directly())
    runner = object()
    captured = {}

    def fake_prepare(mem, objective, state, **kwargs):
        captured["runner"] = kwargs["ensure_runner"](state, mem)
        return SimpleNamespace(
            execution_task=f"managed: {objective}",
            decision=SimpleNamespace(vertical="research", workflow_mode="bounded"),
        )

    monkeypatch.setattr(front_door, "prepare_manager_execution_task", fake_prepare)

    routed = ensure_manager_decision(
        memory,
        item,
        ensure_runner=lambda _state, _mem: runner,
    )

    assert captured["runner"] is runner
    assert routed.objective == "managed: read the literature"
    assert routed.manager_decision == {
        "vertical": "research",
        "workflow_mode": "bounded",
        "require_independent_review": True,
        "routed": True,
    }


def test_guard_reroutes_unknown_persisted_vertical(tmp_path, monkeypatch) -> None:
    from argus_skill.life.memory import LifeMemory
    from argus_skill.manager import front_door

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
    from argus_skill.apps import _life_actions

    signature = inspect.signature(_life_actions.add_backlog_item)

    assert "manager_decision" in signature.parameters


def test_the_supervisor_routes_before_executing() -> None:
    from argus_skill.life.supervisor import _mission_execution

    source = inspect.getsource(_mission_execution)
    claim_at = source.index("claim_next()")
    guard_at = source.index("ensure_manager_decision(")
    context_at = source.index("_prepare_mission_context(")

    # Must happen after the claim and before the mission context is built,
    # or the run proceeds under the default workflow.
    assert claim_at < guard_at < context_at
    assert "_bound_manager()" in source
    assert "manager=manager" in source


def test_status_reports_bypassed_items() -> None:
    from argus_skill.apps.cli import _core

    source = inspect.getsource(_core)

    # Nothing errors when the Manager is bypassed, so --status has to say it
    # or the blindness stays invisible.
    assert "describe_undecided" in source


def test_bounded_dispatch_records_manager_decision_on_planner_nodes(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.life import MemoryBundle
    from argus_skill.manager import dispatch, front_door

    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-backlog-guard",
    )
    mem.init()

    class Manager:
        def decide_vertical(self, body: str, **_kwargs):
            return SimpleNamespace(
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

    plan = SimpleNamespace(
        reason="two generic DAG nodes",
        error="",
        tasks=(
            SimpleNamespace(
                key="statement-lock",
                deps=(),
                title="Lock statement",
                objective="Wait for operator approval before locking.",
            ),
            SimpleNamespace(
                key="exact-search",
                deps=("statement-lock",),
                title="Exact search",
                objective="Search only after the statement is approved.",
            ),
        ),
    )
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda _state, _mem: SimpleNamespace(manager=Manager()),
    )
    monkeypatch.setattr(dispatch, "_plan_bounded_execution", lambda *a, **k: plan)

    dispatch.enqueue_mission(
        mem,
        "operator-approved maintenance increment",
        {"backend": "memory"},
        root_task_id="root-manager-decision",
    )

    items = mem.backlog.all()
    assert [item.node_key for item in items] == ["statement-lock", "exact-search"]
    assert all(item.manager_decision.get("routed") is True for item in items)
    assert all(item.manager_decision.get("workflow_mode") == "bounded" for item in items)
    assert all(needs_manager_decision(item) is False for item in items)
