"""The integrity CLI, run over a paper project the way the checklists do.

`run.score_variance` used to hand the agent a `jq | sort -u | wc -l` pipeline
and ask it to interpret the number; `draft.bibliography` asked for a
"verification log". Both are now a command with an exit code.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research import integrity_check as mod
from argus_skill.verticals.research.stages import stage_completion_issues

GOOD_BIB = (
    "@article{smith2024, author={Smith, Jane and Doe, John}, "
    "title={A Real Paper}, year={2024}}\n"
)


def _paper(tmp_path: Path, tex: str, bib: str) -> Path:
    paper = tmp_path / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    (paper / "main.tex").write_text(tex, encoding="utf-8")
    (paper / "references.bib").write_text(bib, encoding="utf-8")
    return tmp_path


def _scored(tmp_path: Path, scores: list[float], family: str = "f1") -> Path:
    path = tmp_path / "experiments" / "runs" / "r1" / "results" / family / "scored_rows.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps({"id": i, "score": s}) for i, s in enumerate(scores)),
        encoding="utf-8",
    )
    return tmp_path


# -- citations --------------------------------------------------------------

def test_a_resolving_bibliography_passes(tmp_path: Path) -> None:
    root = _paper(tmp_path, r"\cite{smith2024}", GOOD_BIB)

    assert mod.main(["citations", "--project-root", str(root)]) == 0


def test_an_unresolved_citation_fails(tmp_path: Path, capsys) -> None:
    root = _paper(tmp_path, r"\cite{ghost2025}", GOOD_BIB)

    assert mod.main(["citations", "--project-root", str(root)]) == 2
    assert "unresolved_citation" in capsys.readouterr().err


def test_an_unverified_entry_fails(tmp_path: Path, capsys) -> None:
    root = _paper(
        tmp_path,
        r"\cite{k}",
        "@article{k, author={A}, title={VERIFY_CITATION}, year={2020}}",
    )

    assert mod.main(["citations", "--project-root", str(root)]) == 2
    assert "unverified_bib_entry" in capsys.readouterr().err


def test_uncited_entries_do_not_fail_by_default(tmp_path: Path) -> None:
    root = _paper(tmp_path, "no citations here", GOOD_BIB)

    assert mod.main(["citations", "--project-root", str(root)]) == 0


def test_uncited_entries_are_reported_when_requested(tmp_path: Path, capsys) -> None:
    root = _paper(tmp_path, "no citations here", GOOD_BIB)

    # Advisory: still exit 0, but say so.
    assert mod.main(["citations", "--project-root", str(root), "--require-all-cited"]) == 0
    assert "uncited_bib_entry" in capsys.readouterr().out


def test_a_project_without_a_paper_is_not_a_failure(tmp_path: Path) -> None:
    assert mod.main(["citations", "--project-root", str(tmp_path)]) == 0


def test_tex_and_bib_outside_a_paper_dir_are_still_checked(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(r"\cite{ghost}", encoding="utf-8")
    (tmp_path / "refs.bib").write_text(GOOD_BIB, encoding="utf-8")

    assert mod.main(["citations", "--project-root", str(tmp_path)]) == 2


def test_only_main_reachable_tex_is_checked(tmp_path: Path) -> None:
    root = _paper(
        tmp_path,
        r"\cite{smith2024}\bibliography{references}",
        GOOD_BIB,
    )
    build = root / "paper" / "pdf_build"
    build.mkdir()
    (build / "stale.tex").write_text(r"\cite{ghost2025}", encoding="utf-8")

    assert mod.main(["citations", "--project-root", str(root)]) == 0


def test_reachable_input_is_checked(tmp_path: Path, capsys) -> None:
    root = _paper(
        tmp_path,
        r"\input{sections/body}\bibliography{references}",
        GOOD_BIB,
    )
    sections = root / "paper" / "sections"
    sections.mkdir()
    (sections / "body.tex").write_text(r"\cite{ghost2025}", encoding="utf-8")

    assert mod.main(["citations", "--project-root", str(root)]) == 2
    assert "unresolved_citation" in capsys.readouterr().err


def test_addbibresource_selects_the_declared_bibliography(tmp_path: Path) -> None:
    root = _paper(
        tmp_path,
        r"\cite{smith2024}\addbibresource[location=local]{sources.bib}",
        "@article{unused, author={A}, title={Unused}, year={2024}}",
    )
    (root / "paper" / "sources.bib").write_text(GOOD_BIB, encoding="utf-8")

    assert mod.main(["citations", "--project-root", str(root)]) == 0


def test_missing_declared_bibliography_fails_even_when_other_bib_exists(
    tmp_path: Path,
    capsys,
) -> None:
    root = _paper(
        tmp_path,
        r"\cite{smith2024}\bibliography{missing}",
        GOOD_BIB,
    )

    assert mod.main(["citations", "--project-root", str(root)]) == 2
    assert "missing_bibliography" in capsys.readouterr().err


def test_declared_bibliography_cannot_escape_paper_root(
    tmp_path: Path,
    capsys,
) -> None:
    root = _paper(tmp_path, r"\bibliography{../outside}", GOOD_BIB)
    (root / "outside.bib").write_text(GOOD_BIB, encoding="utf-8")

    assert mod.main(["citations", "--project-root", str(root)]) == 2
    assert "bibliography_path_escape" in capsys.readouterr().err


def test_commented_bibliography_declaration_is_ignored(tmp_path: Path) -> None:
    root = _paper(
        tmp_path,
        "\\cite{smith2024}\n% \\bibliography{missing}\n",
        GOOD_BIB,
    )

    assert mod.main(["citations", "--project-root", str(root)]) == 0


def test_late_stages_enforce_citation_integrity(tmp_path: Path) -> None:
    root = _paper(tmp_path, r"\cite{ghost2025}", GOOD_BIB)

    for stage in ("draft", "review", "submission"):
        issues = stage_completion_issues(stage, root)
        assert any(
            "[citation_integrity:unresolved_citation]" in issue
            for issue in issues
        ), (stage, issues)


# -- scores -----------------------------------------------------------------

def test_a_discriminating_scorer_passes(tmp_path: Path) -> None:
    root = _scored(tmp_path, [0.1, 0.4, 0.9, 0.2])

    assert mod.main(["scores", "--project-root", str(root)]) == 0


def test_a_constant_scorer_fails(tmp_path: Path, capsys) -> None:
    root = _scored(tmp_path, [1.0, 1.0, 1.0, 1.0])

    assert mod.main(["scores", "--project-root", str(root)]) == 2
    err = capsys.readouterr().err
    assert "constant_scorer" in err
    # The offending file is named so the fix is obvious.
    assert "scored_rows.jsonl" in err


def test_three_identical_rows_are_not_yet_suspicious(tmp_path: Path) -> None:
    # The checklist says ">3 rows"; below that, identical scores are plausible.
    root = _scored(tmp_path, [1.0, 1.0, 1.0])

    assert mod.main(["scores", "--project-root", str(root)]) == 0


def test_every_family_is_checked(tmp_path: Path, capsys) -> None:
    _scored(tmp_path, [0.1, 0.5, 0.7, 0.9], family="good")
    root = _scored(tmp_path, [0.5] * 6, family="stubbed")

    assert mod.main(["scores", "--project-root", str(root)]) == 2
    assert "stubbed" in capsys.readouterr().err


def test_malformed_rows_are_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "experiments" / "scored_rows.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"score": 0.1}\nnot json\n{"score": 0.9}\n', encoding="utf-8")

    assert mod.main(["scores", "--project-root", str(tmp_path)]) == 0


def test_no_scored_rows_is_not_a_failure(tmp_path: Path) -> None:
    assert mod.main(["scores", "--project-root", str(tmp_path)]) == 0
