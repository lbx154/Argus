"""Importing a workdir's ``.argus/PIPELINE_STATE.json`` into the state root.

This runs inside ``build_life_runner``, so anything it raises does not surface
as itself: it kills the front-door runner, and the operator is told the Manager
"could not classify this message ... please retry". That makes the difference
between the two failure shapes matter more than it looks — one is a real fault
worth stopping for, and the other was a project that had simply not been
classified yet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.pipeline_state import read_pipeline_state
from argus_skill.skills.vertical_select import (
    VerticalResolutionError,
    migrate_legacy_manager_state,
    resolve_vertical_if_decided,
)


def _write_state(root: Path, payload: dict) -> Path:
    path = root / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    """(state_root, workdir) — the split ``_manager_roots`` hands this."""
    return tmp_path / "session", tmp_path / "project"


# -- the decided project ----------------------------------------------------


def test_a_decided_vertical_is_imported(tmp_path: Path) -> None:
    state_root, workdir = _roots(tmp_path)
    _write_state(workdir, {"vertical": "math", "current_stage": "solve"})

    assert migrate_legacy_manager_state(state_root, workdir) is True

    payload = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert payload["vertical"] == "math"
    assert payload["current_stage"] == "solve"


def test_the_workspace_copy_is_left_alone(tmp_path: Path) -> None:
    state_root, workdir = _roots(tmp_path)
    source = _write_state(workdir, {"vertical": "math"})
    before = source.read_text(encoding="utf-8")

    migrate_legacy_manager_state(state_root, workdir)

    # The workdir copy is also the live evidence root, not just a leftover.
    assert source.read_text(encoding="utf-8") == before


def test_split_research_import_does_not_materialize_handoff_in_source_worktree(
    tmp_path: Path,
) -> None:
    state_root, workdir = _roots(tmp_path)
    source = _write_state(
        workdir,
        {
            "vertical": "research",
            "current_stage": "research",
            "stages": {"research": {"status": "done"}},
        },
    )
    research = workdir / "research"
    research.mkdir(exist_ok=True)
    (research / "IDEA_SELECTION.json").write_text(
        json.dumps({
            "route_id": "route-03",
            "rationale": "legacy source-only winner",
            "evidence_considered": "twelve routes and reviews",
            "resource_requirements": "four accelerators",
        }),
        encoding="utf-8",
    )
    before = source.read_text(encoding="utf-8")

    assert migrate_legacy_manager_state(state_root, workdir) is True

    assert source.read_text(encoding="utf-8") == before
    assert not (workdir / "HANDOFF.md").exists()
    imported = read_pipeline_state(state_root)
    assert imported["current_stage"] == "idea"
    assert imported["selected_idea"]["route_id"] == "route-03"
    assert imported["legacy_selection_consumed"] is True


def test_an_unresolvable_vertical_still_stops_the_build(tmp_path: Path) -> None:
    """Seating a campaign on stages and completion hooks that do not exist
    fails later, somewhere far less legible than here."""
    state_root, workdir = _roots(tmp_path)
    _write_state(workdir, {"vertical": "astrology"})

    with pytest.raises(VerticalResolutionError) as caught:
        migrate_legacy_manager_state(state_root, workdir)

    # Naming the offending value is the whole point: the operator has to know
    # which key to fix, and this message is the only place it appears.
    assert "astrology" in str(caught.value)
    assert not (state_root / ".argus" / "PIPELINE_STATE.json").exists()


# -- the undecided project --------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"math_objective_mode": "targeted", "math_goal": "G"},
    {"research_target_level": "publishable"},
    {"vertical": "", "research_target_level": "publishable"},
    {"vertical": None, "math_objective_mode": "exploratory"},
])
def test_state_without_a_vertical_is_imported_not_refused(
    tmp_path: Path, payload: dict
) -> None:
    """The observed brick: an operator sets the math objective mode on a fresh
    project, and the very first message comes back "could not classify ...
    please retry" — advice that can never succeed, because nothing about the
    project changes between retries.
    """
    state_root, workdir = _roots(tmp_path)
    _write_state(workdir, payload)

    assert migrate_legacy_manager_state(state_root, workdir) is True

    imported = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    for key, value in payload.items():
        assert imported[key] == value


def test_importing_undecided_state_does_not_seat_a_vertical(tmp_path: Path) -> None:
    """Importing must not decide anything: the Manager still owns that call,
    and a silently seated ``research`` would outrank its classification."""
    state_root, workdir = _roots(tmp_path)
    _write_state(workdir, {"math_objective_mode": "exploratory"})

    migrate_legacy_manager_state(state_root, workdir)

    assert resolve_vertical_if_decided(state_root) is None


def test_the_objective_survives_the_import(tmp_path: Path) -> None:
    """End of the chain this was blocking: the mode the operator chose has to
    be readable from the root the Manager was handed."""
    from argus_skill.verticals.math.objective_mode import resolve_objective, set_objective

    state_root, workdir = _roots(tmp_path)
    set_objective(workdir, mode="targeted", goal="the pentagon bound is sharp")

    migrate_legacy_manager_state(state_root, workdir)

    assert resolve_objective(state_root).goal == "the pentagon bound is sharp"


# -- when it declines to run ------------------------------------------------


def test_an_existing_target_is_never_overwritten(tmp_path: Path) -> None:
    state_root, workdir = _roots(tmp_path)
    _write_state(state_root, {"vertical": "math", "current_stage": "review"})
    _write_state(workdir, {"vertical": "research", "current_stage": "scope"})

    assert migrate_legacy_manager_state(state_root, workdir) is False

    payload = json.loads(
        (state_root / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert payload["current_stage"] == "review"


def test_nothing_to_import_is_not_an_error(tmp_path: Path) -> None:
    state_root, workdir = _roots(tmp_path)
    workdir.mkdir(parents=True)

    assert migrate_legacy_manager_state(state_root, workdir) is False


def test_one_root_serving_as_both_is_a_no_op(tmp_path: Path) -> None:
    _write_state(tmp_path, {"vertical": "math"})

    assert migrate_legacy_manager_state(tmp_path, tmp_path) is False


def test_corrupt_source_state_is_reported(tmp_path: Path) -> None:
    state_root, workdir = _roots(tmp_path)
    path = workdir / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(VerticalResolutionError, match="not valid JSON"):
        migrate_legacy_manager_state(state_root, workdir)
