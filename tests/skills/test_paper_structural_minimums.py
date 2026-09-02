from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals.research.paper_structural_minimums import (
    validate_paper_structural_minimums,
)
from argus_skill.verticals.research.stages import stage_completion_issues


def _paper(root: Path, *, missing_figure: bool = False) -> None:
    paper = root / "paper"
    figures = paper / "figures"
    figures.mkdir(parents=True)
    if not missing_figure:
        (figures / "method_overview.pdf").write_bytes(b"%PDF-1.4\n")
    keys = [f"work{i}" for i in range(8)]
    related = " ".join(
        "Accepted work establishes the comparison and evaluation protocol."
        for _ in range(20)
    )
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        f"\\section{{Introduction}}Claim \\cite{{{','.join(keys)}}}.\n"
        "\\begin{figure}\\includegraphics{figures/method_overview.pdf}"
        "\\caption{Method overview and executed path.}\\end{figure}\n"
        f"\\section{{Related Work}}{related}\n"
        "\\section{Method}The method follows the stated mechanism.\n"
        "\\section{Results}\\begin{table}\\caption{Official score; higher is better.}"
        "\\begin{tabular}{lr}Method&Score\\\\Baseline&71\\\\"
        "\\textbf{Ours}&\\textbf{78}\\\\\\end{tabular}\\end{table}\n"
        "\\section{Conclusion}The primary comparison supports the thesis.\n"
        "\\appendix\\section{Reproducibility}Configuration and controls.\n"
        "\\bibliographystyle{plain}\\bibliography{refs}\\end{document}\n",
        encoding="utf-8",
    )
    (paper / "refs.bib").write_text(
        "\n".join(
            f"@inproceedings{{{key},title={{Strong baseline {index}}},"
            "author={A. Author},year={2025}}}"
            for index, key in enumerate(keys)
        ),
        encoding="utf-8",
    )
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with (paper / "main.pdf").open("wb") as handle:
        writer.write(handle)


def test_direct_paper_structure_passes_without_legacy_manifests(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "research", target_venue="EMNLP")
    _paper(tmp_path)

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()
    assert report.included_overview_figures == [
        "paper/figures/method_overview.pdf"
    ]
    assert not (tmp_path / "paper" / "DRAFT_OUTLINE.md").exists()
    assert not (tmp_path / "paper" / "figures" / "FIGURE_PROVENANCE.json").exists()


def test_missing_included_figure_blocks_paper_completion(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", target_venue="EMNLP")
    _paper(tmp_path, missing_figure=True)
    (tmp_path / "HANDOFF.md").write_text("# HANDOFF — PAPER\n", encoding="utf-8")

    issues = stage_completion_issues("paper", tmp_path)

    assert any("missing_figure_files" in issue for issue in issues)


def test_unresolved_citation_blocks_paper_completion(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", target_venue="EMNLP")
    _paper(tmp_path)
    main = tmp_path / "paper" / "main.tex"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            "\\cite{work0,work1,work2,work3,work4,work5,work6,work7}",
            "\\cite{missing}",
        ),
        encoding="utf-8",
    )
    (tmp_path / "HANDOFF.md").write_text("# HANDOFF — PAPER\n", encoding="utf-8")

    issues = stage_completion_issues("paper", tmp_path)

    assert any("citation_integrity:unresolved_citation" in issue for issue in issues)


def test_split_state_root_supplies_the_venue_contract(tmp_path: Path) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    persist_vertical(state, "research", target_venue="EMNLP")
    _paper(workdir)

    report = validate_paper_structural_minimums(workdir, state_root=state)

    assert report.ok, report.to_text()


def test_review_completion_requires_a_substantive_single_review(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "research", target_venue="EMNLP")
    _paper(tmp_path)
    review = tmp_path / "paper" / "REVIEW.md"
    review.write_text(
        "# Authoritative review\n\n"
        "**Verdict:** done\n\n"
        "## Scientific, visual, and language assessment\n"
        "Scientific: pass; the executed method and evidence support the thesis.\n"
        "Visual: pass; every rendered page, figure, and table is publication-ready.\n"
        "Language: pass; the manuscript is precise, coherent, and polished.\n\n"
        "## Strongest accept case\n"
        "The mechanism, official evaluator, and strong baseline support the thesis.\n\n"
        "## Reject-level issues\nNone.\n\n"
        "## Next action\nNone.\n",
        encoding="utf-8",
    )

    assert stage_completion_issues("review", tmp_path) == ()

    review.write_text(
        "# Authoritative review\n\n"
        "**Verdict:** done\n\n"
        "## Scientific, visual, and language assessment\nAll checks passed.\n\n"
        "## Strongest accept case\n"
        "The mechanism, official evaluator, and strong baseline support the thesis.\n\n"
        "## Reject-level issues\nScientific: pass. Visual: pass. Language: pass.\n\n"
        "## Next action\nNone.\n",
        encoding="utf-8",
    )
    assert stage_completion_issues("review", tmp_path)

    review.write_text("**Verdict:** done\n", encoding="utf-8")
    assert stage_completion_issues("review", tmp_path)
