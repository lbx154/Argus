from __future__ import annotations

from pathlib import Path

from argus_skill.skills.builtins import seed_builtin_skills_for_vertical
from argus_skill.skills.stage_machine import format_full_pipeline_checklist
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    load_vertical_contract,
    vertical_completion_gate,
    vertical_role_banner,
    vertical_workflow_mode,
)


def test_kernel_engineering_is_known_direct_vertical(tmp_path: Path) -> None:
    assert "kernel_engineering" in VERTICALS
    assert require_vertical("kernel_engineering") == "kernel_engineering"
    persist_vertical(tmp_path, "kernel_engineering")

    mod = load_vertical("kernel_engineering")
    assert vertical_completion_gate(mod) == "none"
    assert vertical_workflow_mode(mod) == "direct"
    assert tuple(mod.STAGE_ORDER) == ("optimize",)
    assert mod.STAGE_PRIMARY_DELIVERABLES == {}
    assert "model inference/serving" in VERTICAL_PURPOSES["kernel_engineering"]


def test_kernel_engineering_banner_prioritizes_direct_measured_work() -> None:
    mod = load_vertical("kernel_engineering")
    engineer = vertical_role_banner(mod, "engineer")
    planner = vertical_role_banner(mod, "planner")
    reviewer = vertical_role_banner(mod, "reviewer")

    assert "maximize the real kernel" in engineer
    assert "one coherent implementation" in engineer
    assert "Treat unattended benchmark and profiler runs as asynchronous" in engineer
    assert "Do not foreground-poll" in engineer
    assert "fill spare mission slots" in planner
    assert "`parallel_safe=true`" in planner
    assert "`owns_paths` disjoint" in planner
    assert "never queue status polling" in planner
    assert "Proactively use fresh primary-source research" in planner
    assert "bounded report-only source-analysis task" in planner
    assert "do not wait for repeated failures" in planner
    assert "does not need to produce code" in planner
    assert "portfolio of genuinely different mechanisms" in planner
    assert "Prefer expected upside and information gain over low execution risk" in planner
    assert "one clean screen is enough" in planner
    assert "keep one conversion lane active" in planner
    assert "other slots remain free for unconstrained exploration" in planner
    assert "continue through real runtime wiring" in engineer
    assert "Do not prefer the smallest patch" in engineer
    assert "produce a concise decision-useful research report" in engineer
    assert "immediate verification" in engineer
    assert "immediate reproducibility" in engineer
    assert "high-uncertainty mechanisms over low-risk incrementalism" in engineer
    assert "do not default to multi-seed" in engineer
    assert "report-only research mission is valid" in reviewer
    assert "not-yet-reproducible idea" in reviewer
    assert "Never demand multiple seeds" in reviewer
    assert "never fail work merely because" in reviewer
    assert "process documents" in engineer


def test_kernel_optimization_missions_have_live_search_available() -> None:
    contract = load_vertical_contract("kernel_engineering")
    default = frozenset({"research"})

    assert contract.live_search_stages(
        default,
        preserve_configured=False,
    ) == frozenset({"optimize"})


def test_kernel_engineering_checklist_has_no_process_artifact_stages(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "kernel_engineering")
    text = format_full_pipeline_checklist(role="reviewer", project_root=tmp_path)

    assert "### optimize" in text
    assert "optimize.measured_change" in text
    assert "KERNEL_SCOPE.md" not in text
    assert "ALGORITHM_PLAN.md" not in text
    assert "ENVIRONMENT_AUDIT" not in text
    assert "### submission" not in text
    assert "at least 10 recent high-quality papers" not in text


def test_kernel_engineering_vertical_skills_are_packaged(tmp_path: Path) -> None:
    written = seed_builtin_skills_for_vertical(
        tmp_path,
        "kernel_engineering",
        overwrite=True,
    )
    assert written
    engineer = tmp_path / "engineer" / "kernel-environment-first-engineering.md"
    reviewer = tmp_path / "reviewer" / "kernel-engineering-review.md"
    assert engineer.is_file()
    assert reviewer.is_file()
    engineer_text = engineer.read_text(encoding="utf-8").lower()
    reviewer_text = reviewer.read_text(encoding="utf-8").lower()
    assert "without framework paperwork" in engineer_text
    assert "do not create scope documents" in engineer_text
    assert "bounded research mission" in engineer_text
    assert "code is optional" in engineer_text
    assert "do not prefer the smallest patch" in engineer_text
    assert "immediate verifiability" in engineer_text
    assert "immediate reproducibility" in engineer_text
    assert "expected upside and information gain over low risk" in engineer_text
    assert "do not default to" in engineer_text
    assert "multiple seeds, repeated controls" in engineer_text
    assert "without requiring process documents" in reviewer_text
    assert "report-only research mission" in reviewer_text
    assert "not-yet-reproducible ideas" in reviewer_text
    assert "one clean exploratory screen is sufficient" in reviewer_text
    assert "never block completion" in reviewer_text


def test_kernel_reference_guidance_does_not_gate_exploration() -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / "argus_skill"
        / "verticals"
        / "kernel_engineering"
    )
    frontier = (root / "references" / "frontier-search-protocol.md").read_text()
    idgl = (root / "references" / "idgl-loop.md").read_text()
    measurement = (
        root
        / "skills"
        / "engineer"
        / "kernel-benchmark-measurement-integrity.md"
    ).read_text()

    assert "do not require a failure" in frontier
    assert "valid result even without implementation" in frontier
    assert "not a gate on research" in idgl
    assert "high-risk exploration" in idgl
    assert "One clean run is enough for exploratory screening" in idgl
    assert "do not require multiple seeds" in measurement
