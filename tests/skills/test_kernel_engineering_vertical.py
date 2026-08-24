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

    assert "improve the real kernel" in engineer
    assert "one coherent implementation" in engineer
    assert "Treat unattended benchmark and profiler runs as asynchronous" in engineer
    assert "Do not foreground-poll" in engineer
    assert "fill spare mission slots" in planner
    assert "`parallel_safe=true`" in planner
    assert "`owns_paths` disjoint" in planner
    assert "never queue status polling" in planner
    assert "never fail work merely because" in reviewer
    assert "process documents" in engineer


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
    assert "without requiring process documents" in reviewer_text
    assert "never block completion" in reviewer_text


def test_kernel_optimize_stage_has_no_framework_file_gate() -> None:
    mod = load_vertical("kernel_engineering")
    commands = "\n".join(command for _label, command in mod.STAGE_CHECKS["optimize"])
    assert commands == ""


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
