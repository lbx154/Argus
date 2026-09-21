from __future__ import annotations

from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.manager._core import Manager
from argus.skills.builtins import seed_builtin_skills_for_vertical
from argus.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
)
from argus.verticals._base import load_vertical_contract
from argus.verticals.argus_maintenance.architecture_audit import scan_repository


def test_argus_maintenance_contract_is_built_in(tmp_path: Path) -> None:
    assert "argus_maintenance" in VERTICALS
    assert "Argus" in VERTICAL_PURPOSES["argus_maintenance"]
    persist_vertical(tmp_path, "argus_maintenance")

    contract = load_vertical_contract("argus_maintenance")
    assert contract.stage_order == ("inspect", "change", "verify")
    assert contract.mission_kind == "software"
    assert contract.ground_before_handoff is True
    assert contract.requires_independent_review is True
    assert Manager._kind_for("argus_maintenance") == "software"
    assert Manager._kind_for("software") == "software"
    assert Manager._kind_for("research") == "research"
    assert Manager._kind_for("math_synth") == "optimize"
    # A name that is neither built in nor an installed plugin is a data domain.
    assert Manager._kind_for("ops_continuity_runbook") == "custom"


def test_explicit_vertical_reaches_engineer_and_reviewer_without_pipeline_state(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    seed_builtin_skills_for_vertical(
        skills,
        "argus_maintenance",
        overwrite=True,
    )
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Implemented and verified."))
    backend.queue(
        "reviewer",
        CannedResponse(review_action=('approve_review', {'review': ('Verified.') + '\n\n' + ('None.')})),
    )
    loop = SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            active_vertical="argus_maintenance",
        ),
    )

    outcome = loop.run("Maintain Argus.", workdir=tmp_path, scope="bounded")

    assert outcome.successful
    engineer_prompt, engineer_options = next(
        (prompt, options) for label, prompt, options in backend.history
        if label == "engineer-r1"
    )
    reviewer_prompt, reviewer_options = next(
        (prompt, options) for label, prompt, options in backend.history
        if label == "reviewer"
    )
    engineer_dir = (skills / "engineer").resolve()
    reviewer_dir = (skills / "reviewer").resolve()
    from argus.skills.builtins import builtin_skill_source_path

    bundled = builtin_skill_source_path().resolve()
    for options in (engineer_options, reviewer_options):
        assert {str(bundled), str(bundled / "engineer"), str(bundled / "reviewer")} <= set(options.skill_paths)
    assert [path for path in engineer_options.skill_paths if not Path(path).is_relative_to(bundled)] == [
        str(engineer_dir),
        str(skills.resolve()),
        str(reviewer_dir),
    ]
    assert [path for path in reviewer_options.skill_paths if not Path(path).is_relative_to(bundled)] == [
        str(skills.resolve()),
        str(reviewer_dir),
        str(engineer_dir),
    ]
    assert str(skills.resolve()) in engineer_prompt
    assert str(skills.resolve()) in reviewer_prompt
    assert "Use native Skill descriptions" in engineer_prompt
    assert "Use native Skill descriptions" in reviewer_prompt
    assert "Inspect and simplify Argus" not in engineer_prompt
    assert "Review an Argus maintenance patch" not in reviewer_prompt


def test_argus_maintenance_skills_are_packaged(tmp_path: Path) -> None:
    written = seed_builtin_skills_for_vertical(
        tmp_path,
        "argus_maintenance",
        overwrite=True,
    )

    expected = {
        "engineer/argus-maintenance-execution.md",
        "reviewer/argus-maintenance-review.md",
    }
    assert expected <= written.keys()
    assert all((tmp_path / relative).is_file() for relative in expected)


def test_architecture_audit_surfaces_candidates_without_calling_them_defects(
    tmp_path: Path,
) -> None:
    source = tmp_path / "argus" / "core" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from argus.verticals.research.tool import run\n"
        "GPU = 'B200'\n"
        "HOME = '/home/alice/work'\n"
        "DIGEST = '0123456789abcdef0123456789abcdef'\n"
        "def wrapper(value):\n"
        "    return run(value)\n"
        "def choose(a, b, c):\n"
        "    assert a is not None\n"
        "    try:\n"
        "        return a or b or c\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )
    test_source = tmp_path / "tests" / "test_sample.py"
    test_source.parent.mkdir()
    test_source.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    report = scan_repository(tmp_path)
    categories = report["counts"]["by_category"]

    assert categories["concrete_vertical_import"] == 1
    assert categories["domain_literal_outside_vertical"] == 1
    assert categories["fallback_chain"] == 1
    assert categories["hardcoded_digest"] == 1
    assert categories["machine_specific_path"] == 1
    assert categories["runtime_assert"] == 1
    assert categories["silent_broad_exception"] == 1
    assert categories["thin_wrapper"] == 1
    assert all(row["path"] != "tests/test_sample.py" for row in report["findings"])


def test_architecture_audit_ignores_managed_worktrees(tmp_path: Path) -> None:
    source = tmp_path / "argus" / "core" / "root_candidate.py"
    source.parent.mkdir(parents=True)
    source.write_text("HOME = '/home/alice/work'\n", encoding="utf-8")
    duplicate = (
        tmp_path
        / ".worktrees"
        / "feature"
        / "argus"
        / "core"
        / "root_candidate.py"
    )
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    report = scan_repository(tmp_path)

    assert report["files_scanned"] == 1
    assert report["counts"] == {
        "total": 1,
        "by_category": {"machine_specific_path": 1},
    }
    assert report["findings"] == [
        {
            "category": "machine_specific_path",
            "path": "argus/core/root_candidate.py",
            "line": 1,
            "evidence": "/home/alice",
        }
    ]
