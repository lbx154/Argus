"""Every selectable vertical must have a final stage the Reviewer can certify.

A vertical whose LAST stage has no checklist items and no stage checks renders
as "Checklist not applicable: this stage explicitly declares
`checklist_optional`". The Reviewer is then told there is nothing to verify at
the only place completion is decided, so "done" carries no vertical-owned
meaning at all. ``software`` shipped in exactly that state.

This guard is about the FINAL stage only. Intermediate stages are legitimately
optional — a vertical is free to let work skip straight past them.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from argus_skill.skills.vertical_select import resolve_checklist_vertical

_VERTICALS = Path(__file__).resolve().parents[2] / "argus_skill" / "verticals"


def _selectable_verticals() -> list[str]:
    """Vertical package names that a project can actually resolve to.

    ``direct`` is excluded on purpose: it is a retired capability name that
    ``_known_vertical`` migrates to ``software`` + direct workflow mode, so no
    project can resolve to it and its stage module is never read.
    """
    names = []
    for path in sorted(_VERTICALS.glob("*/stages.py")):
        name = path.parent.name
        if name == "direct":
            continue
        names.append(name)
    return names


def _final_stage_gate(name: str) -> tuple[str, int]:
    module = importlib.import_module(f"argus_skill.verticals.{name}.stages")
    order = list(
        getattr(module, "CHECKLIST_STAGE_ORDER", None)
        or getattr(module, "STAGE_ORDER", [])
        or []
    )
    if not order:
        return "", 0
    final = order[-1]
    items = (getattr(module, "CHECKLIST_ITEMS", {}) or {}).get(final, ()) or ()
    checks = (getattr(module, "STAGE_CHECKS", {}) or {}).get(final, ()) or ()
    return final, len(items) + len(checks)


@pytest.mark.parametrize("vertical", _selectable_verticals())
def test_final_stage_is_certifiable(vertical: str) -> None:
    final, gate_size = _final_stage_gate(vertical)
    assert final, f"{vertical} declares no stages at all"
    assert gate_size > 0, (
        f"{vertical}.{final} is the final stage but has no checklist items and "
        "no stage checks, so the Reviewer is told the checklist is not "
        "applicable and project completion has no vertical-owned meaning. "
        "Give the final stage at least one thing the Reviewer must certify."
    )


def test_direct_is_not_selectable_and_is_migrated_to_software(tmp_path: Path) -> None:
    """Guards the exclusion above: if ``direct`` ever becomes selectable again,
    it needs a real final gate and this test must be revisited."""
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        '{"vertical": "direct", "current_stage": "delivery"}', encoding="utf-8"
    )
    assert resolve_checklist_vertical(tmp_path) == "software"


def test_a_verticals_protected_floor_is_a_subset_of_its_own_checklist() -> None:
    """A protected id that names no real item protects nothing.

    ``checklist_store`` refuses to let Planner ``checklist_ops`` delete or
    weaken an id listed in a vertical's ``PROTECTED_ITEM_IDS``. If such an id
    does not correspond to an actual checklist item the protection is a no-op,
    and the floor silently disappears.
    """
    import importlib

    checked = 0
    for name in _selectable_verticals():
        module = importlib.import_module(f"argus_skill.verticals.{name}.stages")
        protected = getattr(module, "PROTECTED_ITEM_IDS", frozenset()) or frozenset()
        if not protected:
            continue
        declared = {
            getattr(item, "id", None)
            for items in (getattr(module, "CHECKLIST_ITEMS", {}) or {}).values()
            for item in items
        }
        missing = set(protected) - declared
        assert not missing, (
            f"{name}.PROTECTED_ITEM_IDS names {sorted(missing)}, which no "
            "checklist item declares — that floor protects nothing."
        )
        checked += 1
    assert checked, "no vertical declares a protected floor; this guard is inert"
