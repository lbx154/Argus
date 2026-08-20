"""Tests for paper_structural_minimums (venue-floor anti-fab gate).

The gate must reject the v1-style failure mode: PDF compiles but the
LaTeX has zero figures, zero in-text citations, missing Related Work.
That's "passes the validator but isn't actually a paper" — the exact
class of bug this gate exists to catch.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.figure_provenance import register_figure
from argus_skill.verticals.research.paper_structural_minimums import (
    MIN_CITED_BIB_ENTRIES,
    MIN_FIGURES,
    MIN_INTEXT_CITES,
    MIN_RELATED_WORK_CHARS,
    validate_paper_structural_minimums,
)
from argus_skill.verticals.research.stages import stage_completion_issues


def _seed_venue(root: Path) -> None:
    research = root / "research"
    research.mkdir(exist_ok=True)
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "target_venue": "EMNLP"}),
        encoding="utf-8",
    )


def _seed_minimal_passing_paper(root: Path) -> None:
    """Build a minimum-passing paper layout that satisfies every floor."""
    paper = root / "paper"
    paper.mkdir(parents=True)
    _seed_venue(root)
    figures = paper / "figures"
    figures.mkdir()
    # 1 real figure file
    (figures / "fig1.pdf").write_bytes(b"%PDF-1.4 fake\n")
    # teaser + pipeline rasters + manifest (image-2 skills' output)
    (figures / "teaser.png").write_bytes(b"\x89PNG\r\n\x1a\nstub")
    (figures / "pipeline_overview.png").write_bytes(b"\x89PNG\r\n\x1a\nstub")
    (figures / "IMAGE2_FIGURES.json").write_text(
        json.dumps({
            "figures": [
                {"name": "teaser_hero", "file": "paper/figures/teaser.png"},
                {"name": "pipeline_overview",
                 "file": "paper/figures/pipeline_overview.png"},
            ]
        }),
        encoding="utf-8",
    )

    cite_keys = [f"work{i}" for i in range(MIN_INTEXT_CITES)]
    cite_macro = ", ".join(f"\\cite{{{k}}}" for k in cite_keys)
    related_text = "Related work goes here. " * 60  # ~ MIN_RELATED_WORK_CHARS

    (paper / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
\includegraphics{figures/fig1.pdf}
""" + cite_macro + r"""
\section{Related Work}
""" + related_text + r"""
\section{Conclusion}
We conclude.
\appendix
\section{Reproducibility Details}
Hyperparameters and prompts.
\end{document}
""",
        encoding="utf-8",
    )

    bib_lines = []
    for k in cite_keys:
        bib_lines.append(f"@article{{{k}, title={{T}}, author={{A}}, year={{2024}}}}")
    (paper / "refs.bib").write_text("\n".join(bib_lines), encoding="utf-8")


def test_missing_main_tex_fails(tmp_path: Path) -> None:
    report = validate_paper_structural_minimums(tmp_path)
    assert not report.ok
    assert any(i.code == "no_main_tex" for i in report.issues)


def test_minimal_passing_paper_is_ok(tmp_path: Path) -> None:
    _seed_minimal_passing_paper(tmp_path)
    report = validate_paper_structural_minimums(tmp_path)
    assert report.ok, report.to_text()
    assert report.figures_found >= MIN_FIGURES
    assert len(report.cite_keys) >= MIN_INTEXT_CITES
    assert report.bib_entries_cited >= MIN_CITED_BIB_ENTRIES
    assert report.related_work_chars >= MIN_RELATED_WORK_CHARS
    assert report.has_conclusion


def test_appendix_required_via_appendix_command(tmp_path: Path) -> None:
    """`\\appendix` LaTeX command in the seed already — verify it passes."""
    _seed_minimal_passing_paper(tmp_path)
    report = validate_paper_structural_minimums(tmp_path)
    assert report.has_appendix
    assert report.ok, report.to_text()


def test_appendix_required_via_section_title(tmp_path: Path) -> None:
    """`\\section{Appendix ...}` (no `\\appendix` command) also counts."""
    _seed_minimal_passing_paper(tmp_path)
    paper = tmp_path / "paper"
    cite_block = ", ".join(f"\\cite{{work{i}}}" for i in range(MIN_INTEXT_CITES))
    (paper / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
\includegraphics{figures/fig1.pdf}
""" + cite_block + r"""
\section{Related Work}
""" + ("Prior work. " * 120) + r"""
\section{Conclusion}
End.
\section{Appendix A: Reproducibility Details}
Hyperparameters.
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.has_appendix
    assert report.ok, report.to_text()


def test_no_appendix_fails(tmp_path: Path) -> None:
    """Operator policy: every paper must ship with an appendix. A paper
    that has every other floor met but no appendix must fail."""
    _seed_minimal_passing_paper(tmp_path)
    paper = tmp_path / "paper"
    cite_block = ", ".join(f"\\cite{{work{i}}}" for i in range(MIN_INTEXT_CITES))
    (paper / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
\includegraphics{figures/fig1.pdf}
""" + cite_block + r"""
\section{Related Work}
""" + ("Prior work. " * 120) + r"""
\section{Conclusion}
End.
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert not report.has_appendix
    codes = {i.code for i in report.issues}
    assert "no_appendix_section" in codes


def test_v1_style_paper_no_figures_no_cites_fails(tmp_path: Path) -> None:
    """The exact v1 failure mode: PDF compiles, refs.bib has entries, but
    the body has 0 \\includegraphics and only 2 \\cite. Gate must fire."""
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
\cite{onlyone} and \cite{onlytwo}.
\section{Conclusion}
We conclude.
\end{document}
""",
        encoding="utf-8",
    )
    # refs.bib has 10 entries (matches v1 failure shape)
    (paper / "refs.bib").write_text(
        "\n".join(
            f"@article{{ref{i}, title={{T}}, author={{A}}, year={{2024}}}}"
            for i in range(10)
        ),
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)
    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "no_figures" in codes
    assert "too_few_citations" in codes
    assert "too_few_bib_entries_cited" in codes
    assert "no_related_work_section" in codes
    assert "no_appendix_section" in codes
    assert "missing_figure_provenance_manifest" not in codes


def test_result_plot_does_not_substitute_for_figure1_overview(tmp_path: Path) -> None:
    _seed_minimal_passing_paper(tmp_path)
    paper = tmp_path / "paper"
    (paper / "figures" / "result_accuracy.pdf").write_bytes(b"%PDF result\n")
    main = paper / "main.tex"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            "figures/fig1.pdf",
            "figures/result_accuracy.pdf",
        ),
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.figures_found == 1
    assert report.included_overview_figures == []
    assert "missing_figure1_overview" in {issue.code for issue in report.issues}


def test_deterministic_method_overview_needs_no_image2_manifest(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    paper = tmp_path / "paper"
    (paper / "figures" / "IMAGE2_FIGURES.json").unlink()
    (paper / "figures" / "method_framework.svg").write_text(
        "<svg viewBox='0 0 100 60'><text x='5' y='20'>Method</text></svg>\n",
        encoding="utf-8",
    )
    main = paper / "main.tex"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            r"\includegraphics{figures/fig1.pdf}",
            r"\includesvg{figures/method_framework.svg}",
        ),
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()
    assert report.included_overview_figures == [
        "paper/figures/method_framework.svg"
    ]


def test_late_research_stages_run_the_structural_gate(tmp_path: Path) -> None:
    for stage in ("draft", "review", "submission"):
        issues = stage_completion_issues(stage, tmp_path)
        assert any("[no_main_tex]" in issue for issue in issues), (stage, issues)

    assert stage_completion_issues("analysis", tmp_path) == ()


def test_missing_teaser_is_reviewer_judgment_not_structural_failure(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    # overwrite manifest with only a pipeline entry
    (tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({
            "figures": [
                {"name": "pipeline_overview",
                 "file": "paper/figures/pipeline_overview.png"},
            ]
        }),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    codes = {i.code for i in report.issues}
    assert "missing_teaser_figure" not in codes
    assert report.ok, report.to_text()
    assert report.has_pipeline_figure is True
    assert report.has_teaser_figure is False


def test_missing_pipeline_is_reviewer_judgment_not_structural_failure(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    (tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({
            "figures": [
                {"name": "hero_teaser", "file": "paper/figures/teaser.png"},
            ]
        }),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    codes = {i.code for i in report.issues}
    assert "missing_pipeline_figure" not in codes
    assert report.ok, report.to_text()


def test_image2_entry_pointing_at_missing_file_does_not_count(tmp_path: Path) -> None:
    """Anti-fab: a manifest entry whose `file` is a phantom path must
    not satisfy the role floor — same principle as \\includegraphics."""
    _seed_minimal_passing_paper(tmp_path)
    (tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({
            "figures": [
                {"name": "teaser_hero", "file": "paper/figures/ghost_teaser.png"},
                {"name": "pipeline", "file": "paper/figures/ghost_pipeline.png"},
            ]
        }),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    codes = {i.code for i in report.issues}
    assert "missing_teaser_figure" not in codes
    assert "missing_pipeline_figure" not in codes
    assert "invalid_figure_provenance" not in codes
    # Both manifest entries logged as missing_file in the role summary
    assert report.image2_role_summary.get("teaser_hero") == "missing_file"
    assert report.image2_role_summary.get("pipeline") == "missing_file"


def test_malformed_legacy_manifest_is_not_a_structural_blocker(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    manifest = tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json"
    manifest.write_text("{not json", encoding="utf-8")

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()


def test_renderer_neutral_manifest_accepts_non_image2_figure(tmp_path: Path) -> None:
    _seed_minimal_passing_paper(tmp_path)
    figures = tmp_path / "paper" / "figures"
    (figures / "IMAGE2_FIGURES.json").unlink()
    source = figures / "fig1.spec.json"
    source.write_text('{"figure": 1}\n', encoding="utf-8")
    register_figure(
        project_root=tmp_path,
        figure_id="fig1",
        role="method",
        renderer="react-recharts-svg",
        source_path=source,
        output_path=figures / "fig1.pdf",
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()
    assert report.figure_renderer_summary == {"fig1": "react-recharts-svg"}
    assert report.image2_manifest_path is None


def test_canonical_manifest_coverage_is_not_a_structural_gate(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    figures = tmp_path / "paper" / "figures"
    (figures / "IMAGE2_FIGURES.json").unlink()
    source = figures / "different.spec.json"
    output = figures / "different.svg"
    source.write_text("{}\n", encoding="utf-8")
    output.write_text("<svg viewBox='0 0 1 1'/>\n", encoding="utf-8")
    register_figure(
        project_root=tmp_path,
        figure_id="different",
        role="other",
        renderer="figure-spec",
        source_path=source,
        output_path=output,
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()


def test_canonical_image2_metadata_is_not_a_structural_gate(
    tmp_path: Path,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    figures = tmp_path / "paper" / "figures"
    (figures / "IMAGE2_FIGURES.json").unlink()
    prompt = figures / "fig1.prompt.txt"
    prompt.write_text("prompt\n", encoding="utf-8")
    canonical_entry = register_figure(
        project_root=tmp_path,
        figure_id="fig1",
        role="method",
        renderer="image2",
        source_path=prompt,
        output_path=figures / "fig1.pdf",
    )

    missing = validate_paper_structural_minimums(tmp_path)
    assert missing.ok, missing.to_text()

    (figures / "IMAGE2_FIGURES.json").write_text(
        json.dumps(
            {
                "figures": [
                    {
                        "figure_id": "fig1",
                        "output_path": canonical_entry["output_path"],
                        "output_sha256": canonical_entry["output_sha256"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    matched = validate_paper_structural_minimums(tmp_path)
    assert matched.ok, matched.to_text()


def test_legacy_manifest_symlink_is_not_a_structural_blocker(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    manifest = tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json"
    manifest.unlink()
    outside = tmp_path.parent / "outside-image2-manifest.json"
    outside.write_text(
        json.dumps(
            {
                "figures": [
                    {
                        "figure_id": "outside",
                        "output_path": "paper/figures/fig1.pdf",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest.symlink_to(outside)

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()


def test_canonical_manifest_symlink_is_not_a_structural_blocker(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    _seed_minimal_passing_paper(tmp_path)
    figures = tmp_path / "paper" / "figures"
    (figures / "IMAGE2_FIGURES.json").unlink()
    outside = tmp_path.parent / "outside-figure-directory"
    outside.mkdir(exist_ok=True)
    (figures / "FIGURE_PROVENANCE.json").symlink_to(
        outside,
        target_is_directory=True,
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.ok, report.to_text()


def test_image2_role_keyword_variants_accepted(tmp_path: Path) -> None:
    """Role classification is keyword-substring on the entry name.
    Pipeline-class keywords include method/architecture/framework/system/etc.
    """
    _seed_minimal_passing_paper(tmp_path)
    (tmp_path / "paper" / "figures" / "method_arch.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "paper" / "figures" / "fig1_overview.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({
            "figures": [
                {"name": "fig1_main_overview",
                 "file": "paper/figures/fig1_overview.png"},
                {"name": "method_architecture_diagram",
                 "file": "paper/figures/method_arch.png"},
            ]
        }),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.has_teaser_figure is True  # fig1_* counts
    assert report.has_pipeline_figure is True  # method_/architecture_ counts


def test_includegraphics_referencing_missing_file_does_not_count(tmp_path: Path) -> None:
    """The gate is anti-fab: a \\includegraphics{fake.pdf} pointing at a
    non-existent file must NOT satisfy the figure floor."""
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\includegraphics{ghost.pdf}
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.figures_found == 0
    assert "ghost.pdf" in report.figures_missing_files


def test_image2_entry_with_figure_id_field_classifies(tmp_path: Path) -> None:
    """Live v2 paper-framework-figure-studio-pro writes ``figure_id``
    (not ``name``) in IMAGE2_FIGURES.json. The gate must accept either
    that or the ``id`` fallback."""
    _seed_minimal_passing_paper(tmp_path)
    (tmp_path / "paper" / "figures" / "pipeline_diagram.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "paper" / "figures" / "fig1_teaser.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({"figures": [
            # studio-pro style: figure_id, not name; pipeline keyword only
            {"figure_id": "pipeline_diagram_v2",
             "output_path": "paper/figures/pipeline_diagram.png"},
            # also test plain `id` fallback; teaser keyword only
            {"id": "fig1_teaser_hero",
             "file": "paper/figures/fig1_teaser.png"},
        ]}),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.has_pipeline_figure is True
    assert report.has_teaser_figure is True
    assert report.ok, report.to_text()


def test_includegraphics_resolves_with_or_without_extension(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    (paper / "figures").mkdir(parents=True)
    (paper / "figures" / "arch.pdf").write_bytes(b"%PDF\n")
    (paper / "main.tex").write_text(
        r"""\documentclass{article}\begin{document}
\includegraphics[width=0.5\linewidth]{figures/arch}
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.figures_found == 1


def test_includesvg_counts_as_a_real_figure(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    figures = paper / "figures"
    figures.mkdir(parents=True)
    (figures / "architecture.svg").write_text(
        "<svg viewBox='0 0 10 10'/>\n",
        encoding="utf-8",
    )
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "\\includesvg[width=\\linewidth]{figures/architecture}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.figures_found == 1


def test_section_files_are_scanned(tmp_path: Path) -> None:
    """Cites/figures inside `paper/sections/*.tex` (the ARIS-style layout)
    must count, not just main.tex."""
    paper = tmp_path / "paper"
    sections = paper / "sections"
    figures = paper / "figures"
    _seed_venue(tmp_path)
    sections.mkdir(parents=True)
    figures.mkdir()
    (figures / "method_overview.pdf").write_bytes(b"%PDF\n")
    (figures / "teaser.png").write_bytes(b"\x89PNG\r\n")
    (figures / "pipeline.png").write_bytes(b"\x89PNG\r\n")
    (figures / "IMAGE2_FIGURES.json").write_text(
        json.dumps({"figures": [
            {"name": "teaser", "file": "paper/figures/teaser.png"},
            {"name": "pipeline", "file": "paper/figures/pipeline.png"},
        ]}),
        encoding="utf-8",
    )
    (paper / "main.tex").write_text(
        r"""\documentclass{article}\begin{document}
\input{sections/intro}
\input{sections/related}
\input{sections/conclusion}
\end{document}
""",
        encoding="utf-8",
    )
    cite_block = ", ".join(f"\\cite{{k{i}}}" for i in range(MIN_INTEXT_CITES))
    (sections / "intro.tex").write_text(
        r"\section{Introduction}" + "\n"
        + r"\includegraphics{figures/method_overview.pdf}" + "\n"
        + cite_block,
        encoding="utf-8",
    )
    (sections / "related.tex").write_text(
        r"\section{Related Work}" + "\n" + "Related prose. " * 60,
        encoding="utf-8",
    )
    (sections / "conclusion.tex").write_text(
        r"\section{Conclusion}" + "\nDone.\n"
        + r"\appendix" + "\n"
        + r"\section{Reproducibility}" + "\nDetails.\n",
        encoding="utf-8",
    )
    # bib that covers the cites
    (paper / "references.bib").write_text(
        "\n".join(
            f"@article{{k{i}, title={{T}}, author={{A}}, year={{2024}}}}"
            for i in range(MIN_INTEXT_CITES)
        ),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.ok, report.to_text()


def test_only_reachable_nested_tex_files_are_scanned(tmp_path: Path) -> None:
    _seed_minimal_passing_paper(tmp_path)
    paper = tmp_path / "paper"
    sections = paper / "sections"
    nested = sections / "nested"
    nested.mkdir(parents=True)
    (paper / "figures" / "fig1.pdf").write_bytes(b"%PDF reachable\n")
    (paper / "figures" / "unused.pdf").write_bytes(b"%PDF unused\n")
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "\\input{sections/nested/body}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    cites = " ".join(
        f"\\cite{{work{i}}}" for i in range(MIN_INTEXT_CITES)
    )
    (nested / "body.tex").write_text(
        "\\section{Introduction}\n"
        "\\includegraphics{figures/fig1.pdf}\n"
        f"{cites}\n"
        "\\section{Related Work}\n"
        + ("Prior work. " * 120)
        + "\n\\section{Conclusion}\nDone.\n"
        "\\appendix\n\\section{Reproducibility}\nDetails.\n",
        encoding="utf-8",
    )
    (sections / "unused.tex").write_text(
        "\\includegraphics{figures/unused.pdf}\n",
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)

    assert report.figures_found == 1
    assert report.ok, report.to_text()


def test_cite_inside_comment_does_not_count(tmp_path: Path) -> None:
    """A commented-out \\cite line was the v1 boundary case — make sure
    we strip comments before counting."""
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "% \\cite{ghost1} ghost cite in comment\n"
        "Real text. \\cite{real1}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert "ghost1" not in report.cite_keys
    assert "real1" in report.cite_keys


def test_multi_key_cite_splits_correctly(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"""\documentclass{article}\begin{document}
\citep{a, b, c, d}
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.cite_keys == {"a", "b", "c", "d"}


def test_related_work_too_short_fails(tmp_path: Path) -> None:
    _seed_minimal_passing_paper(tmp_path)
    # Replace with a paper that has a Related Work header but ~0 body.
    paper = tmp_path / "paper"
    cite_block = ", ".join(f"\\cite{{work{i}}}" for i in range(MIN_INTEXT_CITES))
    (paper / "main.tex").write_text(
        r"""\documentclass{article}\begin{document}
\section{Introduction}
\includegraphics{figures/fig1.pdf}
""" + cite_block + r"""
\section{Related Work}
Tiny.
\section{Conclusion}
End.
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert not report.ok
    assert any(i.code == "related_work_too_short" for i in report.issues)


def test_alt_section_titles_recognised(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    _seed_venue(tmp_path)
    (paper / "figures").mkdir(parents=True)
    (paper / "figures" / "method_overview.pdf").write_bytes(b"%PDF\n")
    (paper / "figures" / "teaser.png").write_bytes(b"\x89PNG\r\n")
    (paper / "figures" / "pipeline.png").write_bytes(b"\x89PNG\r\n")
    (paper / "figures" / "IMAGE2_FIGURES.json").write_text(
        json.dumps({"figures": [
            {"name": "teaser", "file": "paper/figures/teaser.png"},
            {"name": "pipeline", "file": "paper/figures/pipeline.png"},
        ]}),
        encoding="utf-8",
    )
    cite_block = ", ".join(f"\\cite{{c{i}}}" for i in range(MIN_INTEXT_CITES))
    (paper / "main.tex").write_text(
        r"""\documentclass{article}\begin{document}
\includegraphics{figures/method_overview.pdf}
""" + cite_block + r"""
\section*{Background and Related Work}
""" + ("Padding. " * 120) + r"""
\section{Conclusions}
Done.
\appendix
\section{Reproducibility}
Settings.
\end{document}
""",
        encoding="utf-8",
    )
    (paper / "refs.bib").write_text(
        "\n".join(
            f"@inproceedings{{c{i}, title={{T}}, author={{A}}, year={{2024}}}}"
            for i in range(MIN_INTEXT_CITES)
        ),
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    assert report.ok, report.to_text()
