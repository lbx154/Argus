"""Verticals API + vertical-aware System-(B) stage checklists.

The auto-research loop runs ONE of several *verticals*, selected by a single
``vertical`` field in ``.argus/PIPELINE_STATE.json``:

* ``research`` (the default) — the full paper pipeline. Its checklist output is
  byte-identical to the historical hard-coded behaviour.
* ``optimize_lab`` — the ``optimize_vertical`` fixture's 4-stage
  (setup/optimize/measure/report) metric vertical; it stands in here for every
  optimize-shaped vertical (the community ``speedrun`` / ``kernelbench`` /
  ``math_synth`` share its stage order).

These tests pin the vertical-native API (the keyword classifier + old
paper|optimize "pipeline mode" shims are gone — the Manager AGENT now decides
the vertical; see tests/manager/):

* ``resolve_vertical`` precedence — persisted Manager-authored data domain >
  explicit non-default env ``ARGUS_SKILL_VERTICAL`` > persisted built-in
  ``vertical`` > RAISE (fail-hard, no default).
* ``persist_vertical`` / ``require_vertical`` reject unknown verticals (raise).
* ``format_full_pipeline_checklist`` renders research's stages by default and
  never lets ``ARGUS_SKILL_VERTICAL`` override the persisted vertical.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.skills.stage_machine import (
    ChecklistLoadState,
    current_stage,
    format_full_pipeline_checklist,
    resolve_stage_checklist_contract,
)
from argus.skills.vertical_select import (
    UnknownVerticalError,
    persist_vertical,
    require_vertical,
    reset_stage_for_new_intent,
    resolve_workflow_mode,
    vertical_reached_own_terminal_stage,
)
from argus.verticals._base import load_vertical_contract
from argus.verticals._data_domain import write_data_domain

RESEARCH_STAGES: tuple[str, ...] = (
    "idea", "experiment", "paper", "review",
)


@pytest.fixture(autouse=True)
def _isolate_forced_vertical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)


def _project(tmp_path: Path, vertical: str | None, *, current: str = "run") -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    payload: dict = {"current_stage": current}
    if vertical is not None:
        payload["vertical"] = vertical
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return tmp_path


def test_research_vertical_review_checklist_is_loaded_and_required(
    tmp_path: Path,
) -> None:
    """Req 15: non-Math regression — research vertical review checklist."""
    persist_vertical(tmp_path, "research")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert len(contract.items) > 0


def test_research_vertical_states_evidence_reuse_in_its_own_guidance(
    tmp_path: Path,
) -> None:

    persist_vertical(tmp_path, "research")

    assert resolve_workflow_mode(tmp_path) == "staged"
    assert "reuse reviewed evidence" in load_vertical_contract("research").banner("planner")


def test_direct_orchestration_overrides_the_research_default(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="direct")

    assert resolve_workflow_mode(tmp_path) == "direct"


# --- resolve_vertical precedence: env > state > research --------------------


# --- fail-hard invariants (no keyword classifier lives here anymore) --------


def test_require_vertical_validates_or_raises(optimize_vertical) -> None:
    assert require_vertical(optimize_vertical) == optimize_vertical
    assert require_vertical("research") == "research"
    with pytest.raises(UnknownVerticalError):
        require_vertical("bogus")


def test_persist_vertical_never_resets_existing_stage(optimize_vertical, tmp_path: Path) -> None:
    # Stage authority belongs to the reviewer agent, not the harness. A stage
    # that is NOT in the (mis)persisted vertical's order — here a research
    # ``run`` stage persisted under an optimize vertical after a
    # classification false-positive — must be PRESERVED, never clobbered to
    # the vertical's first stage (that would be an unauthorized rollback that
    # destroys real pipeline progress).
    root = _project(tmp_path, "research", current="run")

    persist_vertical(root, optimize_vertical)

    payload = json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == optimize_vertical
    assert payload["current_stage"] == "experiment"


def _finished_custom_domain(
    tmp_path: Path, name: str, stage_order: tuple[str, ...],
) -> Path:
    """Write a custom data domain ``name`` and persist PIPELINE_STATE.json so
    it has reached ITS OWN terminal stage (``stage_order[-1]``) with
    ``status="done"`` — i.e. a fully completed project under that vertical,
    exactly like the (real, already-closed) ``ops_continuity_runbook`` custom
    vertical this regression was found against.
    """
    write_data_domain(
        tmp_path, name, stages=list(stage_order),
        checklist_stage_order=list(stage_order), created_by="manager",
    )
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": name,
            "current_stage": stage_order[-1],
            "stages": {s: {"status": "done"} for s in stage_order},
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_reset_stage_for_new_intent_preserves_inprogress_reclassification(
    optimize_vertical,
    tmp_path: Path,
) -> None:
    # Mirror scenario (a): reclassifying the SAME evolving, still-in-progress
    # project (research -> optimize mid-project, current_stage="run", not the
    # vertical's own terminal/done stage) must be a no-op — stage is real
    # progress and must be PRESERVED, exactly like
    # test_persist_vertical_never_resets_existing_stage above, but exercised
    # through reset_stage_for_new_intent (the new collision-guard primitive)
    # rather than persist_vertical alone.
    root = _project(tmp_path, "research", current="run")

    persist_vertical(root, optimize_vertical)
    applied = reset_stage_for_new_intent(
        root, old_vertical="research", new_vertical=optimize_vertical,
    )

    assert applied is False
    payload = json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == optimize_vertical
    assert payload["current_stage"] == "experiment"


def test_reset_stage_for_new_intent_resets_stale_stage_from_finished_prior_vertical(
    tmp_path: Path,
) -> None:
    """The exact bug this regression closes: an OLD custom vertical
    (``ops_continuity_runbook``) whose own LAST stage is ``"done"`` happens to
    share its stage NAME ("review") with a stage in a brand-new intent's
    assigned vertical ("research"'s five-stage order also has "review"). Before
    the fix, ``current_stage()`` would silently accept the stale "review" as
    real progress on the new project (a false stage advance with zero
    underlying evidence). After the fix, resolving the new intent must reset
    ``current_stage`` to the NEW vertical's FIRST stage ("idea"), not
    inherit the stale name.
    """
    root = _finished_custom_domain(
        tmp_path, "ops_continuity_runbook",
        ("investigate", "configure", "dry_run", "document", "review"),
    )

    # Sanity: reproduce the bug's symptom BEFORE any new-intent dispatch —
    # current_stage() already (correctly) resolves "review" under the OLD,
    # still-active custom vertical.
    assert current_stage(root) == "review"

    old_vertical = "ops_continuity_runbook"
    new_vertical = "research"  # brand-new, operator-issued intent's vertical
    assert RESEARCH_STAGES[0] == "idea"
    assert "review" in RESEARCH_STAGES  # sanity: the exact name collision

    # This is what Manager.divide()/commit_domain() do: persist the NEW
    # vertical (seed-only — never resets an existing stage on its own), then
    # run the new collision guard.
    persist_vertical(root, new_vertical)
    assert current_stage(root) == "review"  # would be the bug if left here

    applied = reset_stage_for_new_intent(
        root, old_vertical=old_vertical, new_vertical=new_vertical,
    )

    assert applied is True
    assert current_stage(root) == "idea"

    payload = json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "research"
    assert payload["current_stage"] == "idea"
    # Downstream stages downgraded so the planner does not skip back over
    # them; the fully-inherited-but-unrelated "done" statuses from the OLD
    # vertical's stages are no longer read as this project's progress.
    assert payload["stages"]["review"]["status"] == "pending"
    assert payload["stage_history"][-1]["direction"] == "reset"
    assert "rollback_history" not in payload


def test_reset_stage_for_new_intent_reopens_finished_same_vertical(
    tmp_path: Path,
) -> None:
    """A new task in the same vertical must not inherit terminal completion."""
    stage_order = ("scope", "solve", "review")
    root = _finished_custom_domain(tmp_path, "same_math_family", stage_order)

    assert vertical_reached_own_terminal_stage(root, "same_math_family") is True

    persist_vertical(root, "same_math_family")
    applied = reset_stage_for_new_intent(
        root,
        old_vertical="same_math_family",
        new_vertical="same_math_family",
    )

    assert applied is True
    payload = json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "same_math_family"
    assert payload["current_stage"] == "scope"
    assert vertical_reached_own_terminal_stage(root, "same_math_family") is False
    assert payload["rollback_history"][-1]["from_stage"] == "review"
    assert payload["rollback_history"][-1]["to_stage"] == "scope"


# --- format_full_pipeline_checklist is vertical-aware ----------------------


def test_full_pipeline_defaults_to_research_four_stages(tmp_path: Path) -> None:
    root = _project(tmp_path, "research")
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" in text
    # Research keeps its historical 'final submission gate' header.
    assert "final submission gate" in text
