from __future__ import annotations

from pathlib import Path

from argus_skill.verticals.research.paper_infrastructure_review import (
    REQUIRED_CHECKED_SCOPES,
    generate_paper_infrastructure_review,
)


def test_missing_model_evidence_spans_is_blocking(monkeypatch, tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "main.tex").write_text("\\section{Intro}\nHello.\n", encoding="utf-8")

    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review.collect_latex_source_paths",
        lambda root: (["paper/main.tex"], []),
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._read_source_texts",
        lambda root, paths: {"paper/main.tex": "\\section{Intro}\nHello.\n"},
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._run_model_review",
        lambda **kwargs: {
            "leak_free": True,
            "checked_scope": list(REQUIRED_CHECKED_SCOPES),
            "evidence_spans": [],
            "blocking_issues": [],
            "major_issues": [],
            "revision_directives": [],
        },
    )

    result = generate_paper_infrastructure_review(tmp_path, write=False)

    assert result["structural_status"] == "blocked"
    assert result["evidence_spans"] == []
    codes = {issue["code"] for issue in result["blocking_issues"]}
    assert "model_review_missing_evidence_spans" in codes
