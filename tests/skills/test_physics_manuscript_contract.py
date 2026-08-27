"""Physics keeps one paper outcome check: the requested paper compiled."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.verticals._base import load_vertical
from argus_skill.verticals.physics import manuscript


def _write_compiled_paper(root: Path) -> None:
    (root / "MANUSCRIPT.tex").write_text(
        "\\documentclass{article}\\begin{document}Result\\end{document}\n",
        encoding="utf-8",
    )
    (root / "MANUSCRIPT.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")


def test_stage_order_and_stage_writer_hook_are_retained() -> None:
    physics = load_vertical("physics")
    assert physics.STAGE_ORDER == ("scope", "model", "execute", "review", "manuscript")
    assert callable(physics.stage_completion_issues)
    assert not hasattr(physics, "STAGE_CHECKS")


def test_only_compiled_paper_outcome_is_deterministic(tmp_path: Path) -> None:
    _write_compiled_paper(tmp_path)
    assert manuscript.verify_compiled_manuscript(tmp_path) == []
    assert load_vertical("physics").stage_completion_issues("manuscript", tmp_path) == ()


def test_missing_source_or_pdf_blocks(tmp_path: Path) -> None:
    assert manuscript.verify_compiled_manuscript(tmp_path) == [
        "missing/empty MANUSCRIPT.tex",
        "missing/empty MANUSCRIPT.pdf",
    ]


def test_non_pdf_output_blocks(tmp_path: Path) -> None:
    (tmp_path / "MANUSCRIPT.tex").write_text("source", encoding="utf-8")
    (tmp_path / "MANUSCRIPT.pdf").write_text("not a pdf", encoding="utf-8")
    assert "not a PDF" in " ".join(manuscript.verify_compiled_manuscript(tmp_path))


def test_stale_pdf_blocks(tmp_path: Path) -> None:
    _write_compiled_paper(tmp_path)
    source = tmp_path / "MANUSCRIPT.tex"
    pdf = tmp_path / "MANUSCRIPT.pdf"
    os.utime(pdf, (1, 1))
    os.utime(source, (2, 2))
    assert "older than" in " ".join(manuscript.verify_compiled_manuscript(tmp_path))


def test_non_manuscript_stages_have_no_deterministic_check(tmp_path: Path) -> None:
    physics = load_vertical("physics")
    for stage in ("scope", "model", "execute", "review"):
        assert physics.stage_completion_issues(stage, tmp_path) == ()


def test_stage_machine_blocks_then_completes(tmp_path: Path) -> None:
    from argus_skill.skills.stage_machine import StageCompletionError, complete_final_stage

    state_dir = tmp_path / ".argus"
    state_dir.mkdir()
    state_path = state_dir / "PIPELINE_STATE.json"
    state_path.write_text(
        '{"vertical":"physics","current_stage":"manuscript"}', encoding="utf-8"
    )
    with pytest.raises(StageCompletionError, match="MANUSCRIPT.tex"):
        complete_final_stage(tmp_path, reason="reviewed")

    _write_compiled_paper(tmp_path)
    complete_final_stage(tmp_path, reason="reviewed compiled paper")
    assert '"status": "done"' in state_path.read_text(encoding="utf-8")


def test_role_banner_does_not_read_project_state(tmp_path: Path) -> None:
    physics = load_vertical("physics")
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "arbitrary.json").write_text('{"failures":["x"]}')
    assert physics.role_banner("engineer", project_root=tmp_path) == physics.role_banner("engineer")


def test_checklist_leaves_proxy_requirements_to_reviewer() -> None:
    physics = load_vertical("physics")
    text = " ".join(
        item.statement + " " + item.evidence_hint
        for item in physics.CHECKLIST_ITEMS["manuscript"]
    ).lower()
    assert "manuscript.pdf" in text
    assert "reviewer" in text
    assert "exact" not in text
    assert ">=" not in text


def test_cli_reports_outcome(tmp_path: Path) -> None:
    assert manuscript.main(["check", "--project-root", str(tmp_path)]) == 1
    _write_compiled_paper(tmp_path)
    assert manuscript.main(["check", "--project-root", str(tmp_path)]) == 0
