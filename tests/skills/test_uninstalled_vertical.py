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
from types import SimpleNamespace

import pytest

from argus.apps._runtime_supervisor import (
    _independent_review_required_for_project_root,
)
from argus.life.supervisor._lifecycle import LifecycleMixin
from argus.skills import vertical_select as vs
from argus.verticals import _registry

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


def test_review_requirement_fails_closed_for_an_uninstalled_vertical(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "medical", "current_stage": "review"})

    assert _independent_review_required_for_project_root(root) is True


def test_junk_in_the_vertical_key_is_still_not_a_decision(tmp_path) -> None:
    root = _state(tmp_path, {"vertical": "Not A Vertical!", "current_stage": "idea"})

    assert vs.resolve_vertical_if_decided(root) is None
    assert vs.resolve_vertical(root) == "research"


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
