from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.paper_infrastructure_review import (
    REQUIRED_CHECKED_SCOPES,
    PaperInfrastructureReviewError,
    generate_paper_infrastructure_review,
)
from argus_skill.verticals.research.paper_infrastructure_review import (
    main as paper_infrastructure_review_main,
)


def test_missing_model_evidence_spans_is_blocking(monkeypatch, tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "main.tex").write_text("\\section{Intro}\nHello.\n", encoding="utf-8")
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        '{"vertical":"research","target_venue":"EMNLP"}',
        encoding="utf-8",
    )

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


def test_cli_resolves_venue_from_project_root_not_cwd(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    project = tmp_path / "project"
    paper_dir = project / "paper"
    paper_dir.mkdir(parents=True)
    (paper_dir / "main.tex").write_text("\\section{Intro}\nHello.\n", encoding="utf-8")
    research_dir = project / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        '{"vertical":"research","target_venue":"AAAI"}',
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review.collect_latex_source_paths",
        lambda root: (["paper/main.tex"], []),
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._read_source_texts",
        lambda root, paths: {"paper/main.tex": "\\section{Intro}\nHello.\n"},
    )
    observed = {}

    def fake_run_model_review(**kwargs):
        observed["venue"] = kwargs["venue"].key
        return {
            "leak_free": True,
            "checked_scope": list(REQUIRED_CHECKED_SCOPES),
            "evidence_spans": [
                {
                    "source_path": "paper/main.tex",
                    "line": 1,
                    "quote": "Hello.",
                    "why": "paper-facing prose",
                    "section": "body",
                }
            ],
            "blocking_issues": [],
            "major_issues": [],
            "revision_directives": [],
        }

    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._run_model_review",
        fake_run_model_review,
    )

    rc = paper_infrastructure_review_main(["--project-root", str(project)])

    out = capsys.readouterr().out
    assert rc == 0
    assert observed["venue"] == "AAAI"
    assert json.loads(out)["structural_status"] == "ok"


def test_runner_failure_produces_blocked_review_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "main.tex").write_text(
        "\\section{Intro}\nHello.\n",
        encoding="utf-8",
    )
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        '{"vertical":"research","target_venue":"EMNLP"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review.collect_latex_source_paths",
        lambda root: (["paper/main.tex"], []),
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._read_source_texts",
        lambda root, paths: {"paper/main.tex": "Hello."},
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.paper_infrastructure_review._run_model_review",
        lambda **kwargs: (_ for _ in ()).throw(
            PaperInfrastructureReviewError("runner failed")
        ),
    )

    result = generate_paper_infrastructure_review(tmp_path, write=False)

    assert result["structural_status"] == "blocked"
    assert "model_review_unavailable" in {
        issue["code"] for issue in result["blocking_issues"]
    }
