"""Verticals API + vertical-aware System-(B) stage checklists.

The auto-research loop runs ONE of two *verticals*, selected by a single
``vertical`` field in ``research/PIPELINE_STATE.json``:

* ``research`` (the default) — the full eight-stage paper pipeline. Its
  checklist output is byte-identical to the historical hard-coded behaviour.
* ``speedrun`` — the lean 4-stage (setup/optimize/measure/report)
  numeric-optimization vertical: lower one number (mean val bpb) under a fixed
  wall-clock budget, no paper.

These tests pin the vertical-native API (the keyword classifier + old
paper|optimize "pipeline mode" shims are gone — the Manager AGENT now decides
the vertical; see tests/manager/):

* ``resolve_vertical`` precedence — persisted Manager-authored data domain >
  explicit non-default env ``ARGUS_SKILL_VERTICAL`` > persisted built-in
  ``vertical`` > RAISE (fail-hard, no default).
* ``persist_vertical`` / ``require_vertical`` reject unknown verticals (raise).
* ``format_full_pipeline_checklist`` renders research's 8 stages by default and
  speedrun's 4 stages under ``ARGUS_SKILL_VERTICAL=speedrun``.
* the speedrun reviewer banner is the INNOVATION-COACH override.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_checklists import (
    ChecklistLoadState,
    current_stage,
    format_full_pipeline_checklist,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICALS,
    UnknownVerticalError,
    VerticalResolutionError,
    persist_vertical,
    require_vertical,
    reset_stage_for_new_intent,
    resolve_vertical,
    vertical_reached_own_terminal_stage,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_items,
    vertical_checklist_optional_stages,
    vertical_checklist_stage_order,
)
from argus_skill.verticals._data_domain import write_data_domain
from argus_skill.verticals.speedrun.stages import role_banner as speedrun_role_banner

RESEARCH_STAGES: tuple[str, ...] = (
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
)
SPEEDRUN_STAGES: tuple[str, ...] = ("setup", "optimize", "measure", "report")


@pytest.fixture(autouse=True)
def _isolate_forced_vertical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)


def _project(tmp_path: Path, vertical: str | None, *, current: str = "run") -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    payload: dict = {"current_stage": current}
    if vertical is not None:
        payload["vertical"] = vertical
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return tmp_path


def test_empty_builtin_checklists_are_explicitly_optional() -> None:
    implicit_empty: list[tuple[str, str]] = []
    for vertical in VERTICALS:
        module = load_vertical(vertical)
        items = vertical_checklist_items(module)
        optional = vertical_checklist_optional_stages(module)
        for stage in vertical_checklist_stage_order(module):
            if not items.get(stage) and stage not in optional:
                implicit_empty.append((vertical, stage))

    assert implicit_empty == []


def test_research_vertical_review_checklist_is_loaded_and_required(
    tmp_path: Path,
) -> None:
    """Req 15: non-Math regression — research vertical review checklist."""
    persist_vertical(tmp_path, "research")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert len(contract.items) > 0


# --- resolve_vertical precedence: env > state > research --------------------


def test_resolve_failsoft_when_nothing_persisted(tmp_path: Path) -> None:
    # FAIL-SOFT: a rigid rule must never hard-crash a mission. With nothing
    # persisted, resolve returns the safe default rather than raising.
    assert resolve_vertical(tmp_path / "nope") == "research"
    # ...and a state file with no ``vertical`` field also falls back.
    assert resolve_vertical(_project(tmp_path, None)) == "research"


def test_resolve_raises_on_corrupt_state(tmp_path: Path) -> None:
    # Corruption of Manager-owned state is a REAL fault (distinct from the
    # legitimate "not decided yet" case) — it still raises rather than silently
    # masking a broken state file as fresh.
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(VerticalResolutionError):
        resolve_vertical(tmp_path)


def test_resolve_reads_pipeline_state_vertical(tmp_path: Path) -> None:
    assert resolve_vertical(_project(tmp_path, "speedrun")) == "speedrun"


def test_resolve_env_overrides_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, "research")  # state says research...
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")  # ...env wins.
    assert resolve_vertical(root) == "speedrun"


# --- fail-hard invariants (no keyword classifier lives here anymore) --------


def test_persist_vertical_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(UnknownVerticalError):
        persist_vertical(tmp_path, "not_a_real_vertical")


def test_require_vertical_validates_or_raises() -> None:
    assert require_vertical("kernelbench") == "kernelbench"
    assert require_vertical("research") == "research"
    with pytest.raises(UnknownVerticalError):
        require_vertical("bogus")


def test_kernelbench_keeps_research_as_valid_benchmark_research_stage(tmp_path: Path) -> None:
    root = _project(tmp_path, "kernelbench", current="research")

    persist_vertical(root, "kernelbench")

    payload = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "kernelbench"
    assert payload["current_stage"] == "research"
    assert current_stage(root) == "research"


def test_persist_vertical_never_resets_existing_stage(tmp_path: Path) -> None:
    # Stage authority belongs to the reviewer agent, not the harness. A stage
    # that is NOT in the (mis)persisted vertical's order — here a research
    # ``run`` stage persisted under the speedrun vertical after a
    # classification false-positive — must be PRESERVED, never clobbered to
    # the vertical's first stage (that would be an unauthorized rollback that
    # destroys real pipeline progress).
    root = _project(tmp_path, "research", current="run")

    persist_vertical(root, "speedrun")

    payload = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "speedrun"
    assert payload["current_stage"] == "run"  # preserved, NOT reset to "setup"


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
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": name,
            "current_stage": stage_order[-1],
            "stages": {s: {"status": "done"} for s in stage_order},
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_reset_stage_for_new_intent_preserves_inprogress_reclassification(
    tmp_path: Path,
) -> None:
    # Mirror scenario (a): reclassifying the SAME evolving, still-in-progress
    # project (research -> speedrun mid-project, current_stage="run", not the
    # vertical's own terminal/done stage) must be a no-op — stage is real
    # progress and must be PRESERVED, exactly like
    # test_persist_vertical_never_resets_existing_stage above, but exercised
    # through reset_stage_for_new_intent (the new collision-guard primitive)
    # rather than persist_vertical alone.
    root = _project(tmp_path, "research", current="run")

    persist_vertical(root, "speedrun")
    applied = reset_stage_for_new_intent(
        root, old_vertical="research", new_vertical="speedrun",
    )

    assert applied is False
    payload = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "speedrun"
    assert payload["current_stage"] == "run"  # preserved, untouched


def test_vertical_reached_own_terminal_stage_true_and_false(tmp_path: Path) -> None:
    # False: mid-project, not on the vertical's own last stage.
    root = _project(tmp_path, "research", current="run")
    assert vertical_reached_own_terminal_stage(root, "research") is False

    # False: on the vertical's own last stage, but not marked done.
    root2 = tmp_path / "not_done"
    root2.mkdir()
    (root2 / "research").mkdir(parents=True, exist_ok=True)
    (root2 / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "submission",
            "stages": {"submission": {"status": "in_progress"}},
        }),
        encoding="utf-8",
    )
    assert vertical_reached_own_terminal_stage(root2, "research") is False

    # True: a custom vertical whose own last stage IS current_stage AND done.
    root3 = tmp_path / "finished_custom"
    root3.mkdir()
    _finished_custom_domain(
        root3, "ops_continuity_runbook",
        ("investigate", "configure", "dry_run", "document", "review"),
    )
    assert vertical_reached_own_terminal_stage(root3, "ops_continuity_runbook") is True


def test_reset_stage_for_new_intent_resets_stale_stage_from_finished_prior_vertical(
    tmp_path: Path,
) -> None:
    """The exact bug this regression closes: an OLD custom vertical
    (``ops_continuity_runbook``) whose own LAST stage is ``"done"`` happens to
    share its stage NAME ("review") with a stage in a brand-new intent's
    assigned vertical ("research"'s 8-stage order also has "review"). Before
    the fix, ``current_stage()`` would silently accept the stale "review" as
    real progress on the new project (a false stage advance with zero
    underlying evidence). After the fix, resolving the new intent must reset
    ``current_stage`` to the NEW vertical's FIRST stage ("research"), not
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
    assert new_vertical == RESEARCH_STAGES[0]  # sanity: "research" is stage[0]
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
    assert current_stage(root) == "research"  # new vertical's FIRST stage

    payload = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "research"
    assert payload["current_stage"] == "research"
    # Downstream stages downgraded so the planner does not skip back over
    # them; the fully-inherited-but-unrelated "done" statuses from the OLD
    # vertical's stages are no longer read as this project's progress.
    assert payload["stages"]["review"]["status"] == "pending"
    # Audit trail present (same primitive rollback_stage always uses).
    assert payload["rollback_history"][-1]["rolled_back_by"] == "manager"
    assert payload["rollback_history"][-1]["to_stage"] == "research"


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
    payload = json.loads((root / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["vertical"] == "same_math_family"
    assert payload["current_stage"] == "scope"
    assert vertical_reached_own_terminal_stage(root, "same_math_family") is False
    assert payload["rollback_history"][-1]["from_stage"] == "review"
    assert payload["rollback_history"][-1]["to_stage"] == "scope"


def test_persist_vertical_seeds_first_stage_only_when_missing(tmp_path: Path) -> None:
    # Bootstrap of a fresh state file with no stage yet still gets an initial
    # stage seeded — that is initialization, not control.
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research"}), encoding="utf-8"
    )

    persist_vertical(tmp_path, "research")

    payload = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert payload["current_stage"] == "research"  # research vertical's first stage


def test_kernelbench_research_checklist_is_not_paper_literature_gate(tmp_path: Path) -> None:
    root = _project(tmp_path, "kernelbench", current="research")

    text = format_full_pipeline_checklist(role="reviewer", project_root=root)

    assert "### research" in text
    assert "SOTA-oriented technique research" in text
    assert "research.first_score_plan" in text
    assert "at least 10 recent high-quality papers" not in text


def test_kernelbench_reviewer_skill_paths_exist() -> None:
    from argus_skill.verticals.kernelbench.stages import REVIEWER_CHECKLISTS

    builtin_root = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"
    missing = []
    for stage, (skill_path, _instructions, _files) in REVIEWER_CHECKLISTS.items():
        if not (builtin_root / skill_path).exists():
            missing.append(f"{stage}: {skill_path}")
    assert missing == []


# --- format_full_pipeline_checklist is vertical-aware ----------------------


def test_full_pipeline_defaults_to_research_eight_stages(tmp_path: Path) -> None:
    root = _project(tmp_path, "research")
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" in text
    # Research keeps its historical 'final submission gate' header.
    assert "final submission gate" in text


def test_full_pipeline_checklist_prefers_persisted_vertical_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")
    root = _project(tmp_path, "research")
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" in text
    for stage in SPEEDRUN_STAGES:
        assert f"### {stage}\n" not in text
    assert "final submission gate" in text


# --- speedrun reviewer banner is the innovation-coach override -------------


def test_speedrun_reviewer_banner_is_innovation_coach() -> None:
    banner = speedrun_role_banner("reviewer")
    assert "INNOVATION COACH" in banner


# --- quant (finance factor-research) vertical ------------------------------
#
# ``quant`` is the finance analog of ``research``: a REPORT vertical (it
# produces a reviewer-certified factor report, not a numeric metric), reusing
# the same 8 stage ids with finance semantics. These tests pin that it routes,
# loads, certifies on the full-report gate, and ships its skill files.

QUANT_STAGES: tuple[str, ...] = RESEARCH_STAGES  # same ids, finance semantics


def test_quant_vertical_loads_and_exposes_contract() -> None:
    from argus_skill.verticals._base import load_vertical, vertical_completion_gate

    mod = load_vertical("quant")
    assert tuple(mod.STAGE_ORDER) == QUANT_STAGES
    # Same stage ids drive shell checks and reviewer checklists.
    assert tuple(mod.STAGE_CHECKS.keys()) == QUANT_STAGES
    assert tuple(mod.REVIEWER_CHECKLISTS.keys()) == QUANT_STAGES
    # A factor report is certified on the full-report gate (like research),
    # NOT a numeric speedrun metric.
    assert vertical_completion_gate(mod) == "full_paper"


def test_quant_is_a_report_vertical_not_optimize() -> None:
    # The triage layer must treat quant as a research-shaped REPORT mission, not
    # an optimize one — it produces a certified report, not a tuned number.
    from argus_skill.manager._core import _OPTIMIZE_VERTICALS

    assert "quant" not in _OPTIMIZE_VERTICALS


def test_quant_reviewer_skill_paths_exist() -> None:
    from argus_skill.verticals.quant.stages import REVIEWER_CHECKLISTS

    builtin_root = Path(__file__).resolve().parents[2] / "argus_skill" / "builtin_skills"
    missing = []
    for stage, (skill_path, _instructions, _files) in REVIEWER_CHECKLISTS.items():
        if not (builtin_root / skill_path).exists():
            missing.append(f"{stage}: {skill_path}")
    assert missing == []


def test_quant_full_pipeline_checklist_is_finance_not_paper(tmp_path: Path) -> None:
    root = _project(tmp_path, "quant", current="run")

    text = format_full_pipeline_checklist(role="reviewer", project_root=root)

    # All 8 stages render, with FINANCE checklist items (not the paper floor).
    for stage in QUANT_STAGES:
        assert f"### {stage}\n" in text
    assert "research.hypotheses" in text
    assert "research.go_no_go" in text
    assert "economic" in text  # economic-mechanism mandate
    assert "search ledger" in text  # search-breadth discipline
    # It is a REPORT vertical (full_paper gate) -> keeps the submission-gate
    # header, not the lean "(quant)" optimize header.
    assert "final submission gate" in text
