"""Tests for reviewer_simulation gate (Step 2 — force reviewer-perspective
question lists to be machine-readable and freshness-tied to main.tex)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.verticals.research.reviewer_simulation import (
    MIN_QUESTIONS,
    QUESTIONS_FILENAME,
    validate_reviewer_simulation,
)


def _good_questions(n: int = MIN_QUESTIONS) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-06-03T00:00:00Z",
        "questions": [
            {
                "id": f"Q{i}",
                "question": f"Reviewer question {i}: why this design choice?",
                "severity": ("critical", "major", "minor")[i % 3],
                "addressed_in_section": f"section {i % 4 + 1}",
                "addressed_evidence": f"see Table {i + 1} or Section {i % 4 + 1}",
            }
            for i in range(n)
        ],
    }


def _seed_paper(root: Path, *, with_questions: dict | None) -> Path:
    research = root / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "target_venue": "EMNLP"}),
        encoding="utf-8",
    )
    paper = root / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    main_tex = paper / "main.tex"
    main_tex.write_text(
        "\\documentclass{article}\\begin{document}body\\end{document}\n",
        encoding="utf-8",
    )
    if with_questions is not None:
        (paper / QUESTIONS_FILENAME).write_text(json.dumps(with_questions), encoding="utf-8")
    return main_tex


def test_missing_questions_file_fails(tmp_path: Path) -> None:
    _seed_paper(tmp_path, with_questions=None)
    report = validate_reviewer_simulation(tmp_path)
    assert not report.ok
    assert any(i.code == "missing_reviewer_questions" for i in report.issues)


def test_full_passing_questions_ok(tmp_path: Path) -> None:
    main_tex = _seed_paper(tmp_path, with_questions=_good_questions())
    # ensure questions mtime >= main.tex mtime
    qpath = tmp_path / "paper" / QUESTIONS_FILENAME
    later = main_tex.stat().st_mtime + 5
    os.utime(qpath, (later, later))
    report = validate_reviewer_simulation(tmp_path)
    assert report.ok, report.to_text()
    assert report.questions_found == MIN_QUESTIONS
    assert report.addressed_count == MIN_QUESTIONS
    assert sum(report.severities.values()) == MIN_QUESTIONS


def test_too_few_questions_fails(tmp_path: Path) -> None:
    _seed_paper(tmp_path, with_questions=_good_questions(n=MIN_QUESTIONS - 1))
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "too_few_questions" in codes


def test_unaddressed_question_fails(tmp_path: Path) -> None:
    payload = _good_questions()
    payload["questions"][0]["addressed_in_section"] = ""
    _seed_paper(tmp_path, with_questions=payload)
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "unaddressed_reviewer_questions" in codes


def test_invalid_severity_fails(tmp_path: Path) -> None:
    payload = _good_questions()
    payload["questions"][3]["severity"] = "showstopper"
    _seed_paper(tmp_path, with_questions=payload)
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "invalid_severity" in codes


def test_duplicate_question_id_fails(tmp_path: Path) -> None:
    payload = _good_questions()
    payload["questions"][1]["id"] = payload["questions"][0]["id"]
    _seed_paper(tmp_path, with_questions=payload)
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "duplicate_question_id" in codes


def test_malformed_json_fails(tmp_path: Path) -> None:
    _seed_paper(tmp_path, with_questions=None)
    (tmp_path / "paper" / QUESTIONS_FILENAME).write_text("{not valid", encoding="utf-8")
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "malformed_reviewer_questions" in codes


def test_questions_older_than_main_tex_is_stale(tmp_path: Path) -> None:
    """Stale check: questions written before main.tex's last edit must
    fail (the agent edited the paper but didn't re-simulate)."""
    main_tex = _seed_paper(tmp_path, with_questions=_good_questions())
    qpath = tmp_path / "paper" / QUESTIONS_FILENAME
    # questions written in the past, main.tex now
    earlier = main_tex.stat().st_mtime - 100
    os.utime(qpath, (earlier, earlier))
    report = validate_reviewer_simulation(tmp_path)
    assert report.stale_vs_main_tex is True
    codes = {i.code for i in report.issues}
    assert "reviewer_questions_stale_vs_main_tex" in codes


def test_questions_newer_than_main_tex_is_fresh(tmp_path: Path) -> None:
    main_tex = _seed_paper(tmp_path, with_questions=_good_questions())
    qpath = tmp_path / "paper" / QUESTIONS_FILENAME
    later = main_tex.stat().st_mtime + 100
    os.utime(qpath, (later, later))
    report = validate_reviewer_simulation(tmp_path)
    assert report.stale_vs_main_tex is False
    assert report.ok, report.to_text()


def test_questions_missing_top_level_questions_key_fails(tmp_path: Path) -> None:
    _seed_paper(tmp_path, with_questions={"schema_version": 1})
    report = validate_reviewer_simulation(tmp_path)
    codes = {i.code for i in report.issues}
    assert "malformed_reviewer_questions" in codes


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
