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

* ``resolve_vertical`` precedence — explicit non-default env
  ``ARGUS_SKILL_VERTICAL`` > persisted data-domain under default env
  ``"research"`` > persisted ``vertical`` > RAISE (fail-hard, no default).
* ``persist_vertical`` / ``require_vertical`` reject unknown verticals (raise).
* ``format_full_pipeline_checklist`` renders research's 8 stages by default and
  speedrun's 4 stages under ``ARGUS_SKILL_VERTICAL=speedrun``.
* the speedrun reviewer banner is the INNOVATION-COACH override.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_checklists import current_stage, format_full_pipeline_checklist
from argus_skill.skills.vertical_select import (
    UnknownVerticalError,
    VerticalResolutionError,
    persist_vertical,
    require_vertical,
    resolve_vertical,
)
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


# --- resolve_vertical precedence: env > state > research --------------------


def test_resolve_raises_when_nothing_persisted(tmp_path: Path) -> None:
    # FAIL-HARD: no silent default-to-research. No PIPELINE_STATE at all raises...
    with pytest.raises(VerticalResolutionError):
        resolve_vertical(tmp_path / "nope")
    # ...and a state file with no ``vertical`` field also raises.
    with pytest.raises(VerticalResolutionError):
        resolve_vertical(_project(tmp_path, None))


def test_resolve_raises_on_corrupt_state(tmp_path: Path) -> None:
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


def test_full_pipeline_speedrun_env_yields_four_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")
    root = _project(tmp_path, "research")  # state says research; env forces speedrun.
    text = format_full_pipeline_checklist(role="reviewer", project_root=root)
    for stage in SPEEDRUN_STAGES:
        assert f"### {stage}\n" in text
    # None of the paper-only stages leak in.
    for stage in RESEARCH_STAGES:
        assert f"### {stage}\n" not in text
    # The header names the vertical instead of the paper submission gate.
    assert "(speedrun)" in text
    assert "final submission gate" not in text


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
    assert vertical_completion_gate(mod) == "full_emnlp"


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
    # It is a REPORT vertical (full_emnlp gate) -> keeps the submission-gate
    # header, not the lean "(quant)" optimize header.
    assert "final submission gate" in text
