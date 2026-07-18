from __future__ import annotations

from pathlib import Path

from argus_skill.skills.builtins import seed_builtin_skills_for_vertical
from argus_skill.skills.stage_checklists import format_full_pipeline_checklist
from argus_skill.skills.vertical_select import VERTICALS, persist_vertical, require_vertical
from argus_skill.verticals._base import (
    load_vertical,
    vertical_completion_gate,
    vertical_role_banner,
    vertical_workflow_mode,
)


def test_kernel_engineering_is_known_metric_vertical(tmp_path: Path) -> None:
    assert "kernel_engineering" in VERTICALS
    assert require_vertical("kernel_engineering") == "kernel_engineering"
    persist_vertical(tmp_path, "kernel_engineering")

    mod = load_vertical("kernel_engineering")
    assert vertical_completion_gate(mod) == "metric"
    assert vertical_workflow_mode(mod) == "staged"
    assert tuple(mod.STAGE_ORDER) == (
        "scope",
        "environment",
        "baseline",
        "optimize",
        "validate",
        "report",
    )


def test_kernel_engineering_banner_makes_environment_a_hard_gate() -> None:
    mod = load_vertical("kernel_engineering")
    engineer = vertical_role_banner(mod, "engineer")
    reviewer = vertical_role_banner(mod, "reviewer")

    assert "ENVIRONMENT IS PART OF THE ALGORITHM" in engineer
    assert "audit" in engineer.lower()
    assert "missing" in reviewer.lower()
    assert "not a failed kernel" in engineer.lower()


def test_kernel_engineering_checklist_is_not_paper_pipeline(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "kernel_engineering")
    text = format_full_pipeline_checklist(role="reviewer", project_root=tmp_path)

    assert "### environment" in text
    assert "environment.capability_audit" in text
    assert "environment.specialized_catalog" in text
    assert "environment.infrastructure_reuse" in text
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
    assert "missing compiler" in engineer.read_text(encoding="utf-8").lower()
    assert "hard environment gate" in reviewer.read_text(encoding="utf-8").lower()


def test_reviewer_checklist_skill_paths_exist() -> None:
    mod = load_vertical("kernel_engineering")
    skill_root = (
        Path(__file__).resolve().parents[2]
        / "argus_skill"
        / "verticals"
        / "kernel_engineering"
        / "skills"
    )
    missing = []
    for stage, (skill_path, _instructions, _artifacts) in mod.REVIEWER_CHECKLISTS.items():
        if not (skill_root / skill_path).is_file():
            missing.append(f"{stage}: {skill_path}")
    assert missing == []
