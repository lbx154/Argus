"""Runtime-routing tests: the thin physics vertical is selectable end to end.

Self-contained — every test builds a throwaway project under ``tmp_path`` and
never reads Phase 3 pipeline outputs or any literature-distillation artifact.
These cover the routing surface only (registration, env force, persistence,
planner banner read, Manager kind); no Argus run, benchmark, or demo case.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    explicit_builtin_vertical,
    persist_vertical,
    require_vertical,
    resolve_vertical,
)
from argus_skill.verticals._base import load_vertical, vertical_role_banner


@pytest.fixture(autouse=True)
def _isolate_forced_vertical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep an operator-forced env var from leaking across the resolve tests.
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)


def _state(root: Path) -> dict:
    return json.loads((root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))


def test_physics_is_a_selectable_builtin_vertical() -> None:
    # Requirement (Part 4.1): physics is in the runtime's selectable vertical set,
    # exposed to the Manager's decision prompt, and accepted by require_vertical.
    assert "physics" in VERTICALS
    assert "physics" in VERTICAL_PURPOSES
    assert VERTICAL_PURPOSES["physics"].strip()
    assert require_vertical("physics") == "physics"
    # Purpose keys must stay in sync with VERTICALS (module invariant).
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)


def test_legacy_env_hint_is_inspectable_but_does_not_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Compatibility only: callers may inspect the old hint, but Manager remains
    # the sole authority and an undecided low-level read keeps its safe fallback.
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "physics")
    assert explicit_builtin_vertical() == "physics"
    assert resolve_vertical(tmp_path) == "research"


def test_env_physics_cannot_override_manager_persisted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Formal task routing always follows Manager's persisted classification.
    persist_vertical(tmp_path, "research")
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "physics")
    assert explicit_builtin_vertical() == "physics"
    assert resolve_vertical(tmp_path) == "research"


def test_persist_physics_writes_pipeline_state_and_seeds_scope(tmp_path: Path) -> None:
    # Requirement (Part 4.3): physics can be persisted into PIPELINE_STATE.json,
    # and the fresh-state bootstrap seeds the vertical's FIRST stage ("scope").
    persist_vertical(tmp_path, "physics")

    state = _state(tmp_path)
    assert state["vertical"] == "physics"
    assert state["current_stage"] == "scope"
    # And a plain read-side resolve (no env) returns the persisted physics vertical.
    assert resolve_vertical(tmp_path) == "physics"


def test_planner_resolution_chain_reads_physics_role_banner(tmp_path: Path) -> None:
    # Requirement (Part 4.4): the exact chain planner.py uses
    # (resolve_vertical -> load_vertical -> vertical_role_banner) yields the
    # physics planner banner, not a research/math one.
    persist_vertical(tmp_path, "physics")

    mod = load_vertical(resolve_vertical(tmp_path), project_root=tmp_path)
    assert mod.__name__ == "argus_skill.verticals.physics.stages"

    banner = vertical_role_banner(mod, "planner")
    assert "MISSION TYPE: PHYSICS" in banner
    assert "physics-specific route selection" in banner
    assert "no fixed paper pipeline" in banner


def test_physics_is_custom_kind_not_optimize_not_paper() -> None:
    # Requirement (Part 4): the Manager routes physics as a dynamic/report ("custom")
    # vertical — never a lean optimize loop and never the paper/full_paper kind.
    from argus_skill.manager import Manager
    from argus_skill.manager._helpers import _OPTIMIZE_VERTICALS

    assert Manager._kind_for("physics") == "custom"
    assert "physics" not in _OPTIMIZE_VERTICALS
    # Parity with the sibling dynamic vertical (math), which is also "custom".
    assert Manager._kind_for("physics") == Manager._kind_for("math")
