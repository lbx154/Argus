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

from argus.core.pipeline_state import read_pipeline_state
from argus.skills.vertical_select import (
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


def test_split_research_import_leaves_the_source_worktree_untouched(
    tmp_path: Path,
) -> None:
    from argus.skills.stage_machine import migrate_legacy_research_stage

    state_root, workdir = _roots(tmp_path)
    source = _write_state(
        workdir,
        {
            "vertical": "research",
            "current_stage": "research",
            "stages": {"research": {"status": "done"}},
        },
    )
    before = source.read_text(encoding="utf-8")

    # ``_manager_roots`` runs both, in this order, under the pipeline lock.
    assert migrate_legacy_manager_state(state_root, workdir) is True
    migrate_legacy_research_stage(state_root)

    assert source.read_text(encoding="utf-8") == before
    assert not (workdir / "RESEARCH_NOTES.md").exists()
    assert not (workdir / "HANDOFF.md").exists()
    imported = read_pipeline_state(state_root)
    assert imported["current_stage"] == "idea"


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


def test_importing_undecided_state_does_not_seat_a_vertical(tmp_path: Path) -> None:
    """Importing must not decide anything: the Manager still owns that call,
    and a silently seated ``research`` would outrank its classification."""
    state_root, workdir = _roots(tmp_path)
    _write_state(workdir, {"math_objective_mode": "exploratory"})

    migrate_legacy_manager_state(state_root, workdir)

    assert resolve_vertical_if_decided(state_root) is None


# -- when it declines to run ------------------------------------------------


def test_one_root_serving_as_both_is_a_no_op(tmp_path: Path) -> None:
    _write_state(tmp_path, {"vertical": "math"})

    assert migrate_legacy_manager_state(tmp_path, tmp_path) is False
