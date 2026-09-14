"""A persisted vertical this runtime cannot load is a held project, not a research one.

Before the argus-verticals split every vertical was in the package, so a
persisted name that failed to resolve could only be junk and "not decided"
was the right reading. Now ``PIPELINE_STATE.json`` may say ``vertical: quant``
on a machine without the community package: the decision is real, the
environment is what is missing, and the project must be held with a clear
message rather than quietly re-run as ``research``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from argus_skill.apps._runtime_supervisor import (
    _independent_review_required_for_project_root,
)
from argus_skill.life.supervisor._lifecycle import LifecycleMixin
from argus_skill.skills import vertical_select as vs
from argus_skill.skills.stage_machine import ChecklistItem
from argus_skill.verticals import _registry

INSTALL = 'pip install "argus-verticals @ git+https://github.com/Argus-AiTeam/argus-verticals.git"'


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(_registry, "entry_points", lambda group: [])
    _registry.refresh_vertical_plugins()
    yield
    _registry.refresh_vertical_plugins()


def _state(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_a_persisted_uninstalled_vertical_never_resolves_to_research(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "quant", "current_stage": "run"})

    with pytest.raises(vs.UninstalledVerticalError) as raised:
        vs.resolve_vertical(root)
    with pytest.raises(vs.VerticalResolutionError):
        vs.resolve_vertical_if_decided(root)

    message = str(raised.value)
    assert "'quant'" in message
    assert INSTALL in message
    assert "research" in message  # the available inventory is listed, not substituted
    assert str(root / ".argus" / "PIPELINE_STATE.json") in message


def test_review_requirement_fails_closed_for_an_uninstalled_vertical(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "medical", "current_stage": "review"})

    assert _independent_review_required_for_project_root(root) is True


def test_a_project_with_no_vertical_key_still_defaults_to_research(tmp_path) -> None:
    root = _state(tmp_path, {"current_stage": "idea"})

    assert vs.resolve_vertical_if_decided(root) is None
    assert vs.resolve_vertical(root) == "research"
    assert _independent_review_required_for_project_root(root) is False
    assert vs.resolve_vertical(tmp_path / "never-written") == "research"


def test_junk_in_the_vertical_key_is_still_not_a_decision(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "Not A Vertical!", "current_stage": "idea"})

    assert vs.resolve_vertical_if_decided(root) is None
    assert vs.resolve_vertical(root) == "research"


def test_an_installed_plugin_vertical_resolves_normally(tmp_path, monkeypatch) -> None:
    plugin = ModuleType("plugin.stages")
    plugin.ARGUS_VERTICAL_API_VERSION = 1
    plugin.VERTICAL_PURPOSE = "Plugin work"
    plugin.CHECKLIST_STAGE_ORDER = ("work",)
    plugin.CHECKLIST_ITEMS = {"work": (ChecklistItem("work.output", "Work exists", "artifact"),)}
    plugin.completion_gate = "none"
    entry = SimpleNamespace(name="quant", value="plugin.stages", load=lambda: plugin)
    root = _state(tmp_path, {"vertical": "quant", "current_stage": "work"})
    with pytest.raises(vs.UninstalledVerticalError):
        vs.resolve_vertical(root)

    monkeypatch.setattr(_registry, "entry_points", lambda group: [entry])
    _registry.refresh_vertical_plugins()

    assert vs.resolve_vertical(root) == "quant"
    assert vs.resolve_vertical_if_decided(root) == "quant"


def test_a_project_data_domain_of_that_name_still_resolves(tmp_path) -> None:
    from argus_skill.verticals._data_domain import write_data_domain

    write_data_domain(tmp_path, "quant", stages=["scope", "deliver"])
    root = _state(tmp_path, {"vertical": "quant", "current_stage": "scope"})

    assert vs.resolve_vertical(root) == "quant"


def test_the_skill_scope_keeps_the_persisted_name_while_uninstalled(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "quant", "current_stage": "run"})

    assert vs.resolve_skill_scope(root) == "quant"


# --- the supervisor holds instead of tracing or re-routing -------------------


class _Holder(LifecycleMixin):
    """The mixin under test plus the two hooks the hold path touches, nothing else."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.events: list[dict] = []
        self.statuses: list[str] = []
        self._last_lifecycle_block_sig = None
        self._last_lifecycle_block_at = 0.0

    def _artifact_root(self) -> Path:
        return self._root

    def _lifecycle_root(self) -> Path:
        return self._root

    def _emit(self, event: dict) -> None:
        self.events.append(event)

    def _emit_status(self, text: str) -> None:
        self.statuses.append(text)


def test_the_lifecycle_gate_holds_dispatch_with_the_install_hint(tmp_path) -> None:
    holder = _Holder(_state(tmp_path, {"vertical": "quant", "current_stage": "run"}))
    item = SimpleNamespace(id="item-1", title="continue the factor report")

    first = holder._maybe_block_on_lifecycle(item)
    second = holder._maybe_block_on_lifecycle(item)

    assert first is not None and first["status"] == "vertical_unresolved"
    assert first["item_id"] == "item-1" and INSTALL in first["reason"]
    assert second == first
    blocks = [e for e in holder.events if e["type"] == "life.lifecycle.block"]
    assert len(blocks) == 1  # deduplicated until the heartbeat elapses
    assert blocks[0]["lifecycle_state"] == "vertical_unresolved"
    assert "'quant'" in blocks[0]["reason"]
    assert holder.statuses and "work held" in holder.statuses[0]


def test_the_planning_cycle_holds_and_will_decide_again_later(tmp_path) -> None:
    import inspect

    from argus_skill.life.supervisor._planning_cycle import PlanningCycleMixin
    from argus_skill.life.supervisor._planning_cycle_intake import PlanningCycleIntakeMixin

    root = _state(tmp_path, {"vertical": "quant", "current_stage": "run"})

    class _Cycle(_Holder):
        config = SimpleNamespace(continuous_objective="")
        _vertical_resolved = False

        def _resolve_vertical_once(self):
            return PlanningCycleMixin._resolve_vertical_once(self)

    cycle = _Cycle(root)
    with pytest.raises(vs.UninstalledVerticalError):
        cycle._resolve_vertical_once()
    assert cycle._vertical_resolved is False  # not latched: retried after the operator installs

    # The intake gate turns that raise into a held, backed-off planner cycle
    # instead of a tick error that stops the supervisor. Pinned structurally:
    # the gate's earlier short-circuits need a whole supervisor to drive.
    gate = inspect.getsource(PlanningCycleIntakeMixin._pc_preflight_shortcircuits)
    branch = gate[gate.index("except VerticalResolutionError as exc:"):]
    assert "self._hold_on_unresolved_vertical(exc)" in branch
    assert "self._enter_pause_backoff()" in branch
    assert "return PLAN_ERROR" in branch
