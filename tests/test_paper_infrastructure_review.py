from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.paper_infrastructure_review import (
    REQUIRED_CHECKED_SCOPES,
    PaperInfrastructureReviewError,
    _parse_review_text,
    _review_prompt,
    generate_paper_infrastructure_review,
)
from argus_skill.verticals.research.paper_infrastructure_review import (
    main as paper_infrastructure_review_main,
)
from tests.skills.researched_venues import (
    EIGHT_PAGE_CONFERENCE,
    SEVEN_PAGE_CONFERENCE,
    seed_researched_profile,
)


def test_missing_model_evidence_spans_does_not_become_a_harness_gate(
    monkeypatch, tmp_path: Path
) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "main.tex").write_text("\\section{Intro}\nHello.\n", encoding="utf-8")
    seed_researched_profile(tmp_path, EIGHT_PAGE_CONFERENCE)

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

    assert result["structural_status"] == "ok"
    assert "evidence_spans" not in result
    codes = {issue["code"] for issue in result["blocking_issues"]}
    assert "model_review_missing_evidence_spans" not in codes


def test_prose_review_reads_only_consumed_named_lines() -> None:
    raw = (
        "Major — paper/main.tex:L8 quotes `/root/cache`; move this local path "
        "to supplementary metadata.\n\n"
        "LEAK_FREE=false\n"
        "CHECKED_SCOPE=title; abstract; body; captions\n"
    )

    parsed = _parse_review_text(raw)

    assert parsed["review_text"] == raw
    assert parsed["leak_free"] is False
    assert parsed["checked_scope"] == ["title", "abstract", "body", "captions"]


def test_cli_resolves_venue_from_project_root_not_cwd(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    project = tmp_path / "project"
    paper_dir = project / "paper"
    paper_dir.mkdir(parents=True)
    (paper_dir / "main.tex").write_text("\\section{Intro}\nHello.\n", encoding="utf-8")
    seed_researched_profile(project, SEVEN_PAGE_CONFERENCE)
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
    assert observed["venue"] == "CONFB"
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
    seed_researched_profile(tmp_path, EIGHT_PAGE_CONFERENCE)
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


def test_review_prompt_preserves_complete_middle_source() -> None:
    class DummyVenue:
        reviewer_persona = "systems"

    source = "\n".join(
        [
            "\\section{Start}",
            "opening",
            *[f"middle filler {idx}" for idx in range(1200)],
            "MIDDLE_SENTINEL_LOCAL_PATH_CHECK",
            *[f"tail filler {idx}" for idx in range(1200)],
            "\\section{End}",
            "closing",
        ]
    )

    prompt = _review_prompt(
        source_text_by_path={"paper/main.tex": source},
        venue=DummyVenue(),  # type: ignore[arg-type]
    )

    assert "Complete numbered LaTeX sources:" in prompt
    assert "MIDDLE_SENTINEL_LOCAL_PATH_CHECK" in prompt
    assert "[truncated" not in prompt
    assert "Write a prose review, not JSON" in prompt
    assert "score_1_to_5" not in prompt
    assert "revision_directives" not in prompt
