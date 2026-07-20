from __future__ import annotations

import pytest

from argus_skill.verticals.path_evidence import PathEvidenceError, validate_any_file


def test_path_evidence_requires_nonempty_project_file(tmp_path):
    empty = tmp_path / "attempts" / "a" / "result.json"
    empty.parent.mkdir(parents=True)
    empty.touch()
    with pytest.raises(PathEvidenceError):
        validate_any_file(tmp_path, ["attempts/**/*.json"])

    empty.write_text("{}\n", encoding="utf-8")
    assert validate_any_file(tmp_path, ["attempts/**/*.json"]) == empty


def test_path_evidence_rejects_symlink_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-result.json"
    outside.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "attempts" / "result.json"
    link.parent.mkdir()
    link.symlink_to(outside)
    with pytest.raises(PathEvidenceError):
        validate_any_file(tmp_path, ["attempts/*.json"])


def test_path_evidence_supports_explicit_case_insensitive_globs(tmp_path):
    report = tmp_path / "reports" / "nested" / "deeper" / "TIMING.RPT"
    report.parent.mkdir(parents=True)
    report.write_text("timing met\n", encoding="utf-8")
    assert validate_any_file(
        tmp_path,
        [],
        case_insensitive_patterns=["reports/**/*timing*.rpt"],
    ) == report
