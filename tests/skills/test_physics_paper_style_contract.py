"""Paper-composition-layer contract for the physics manuscript stage.

The terminal manuscript stage now also gates a LaTeX-compiled, journal-style
paper (MANUSCRIPT.tex/pdf + SUPPLEMENT.tex/pdf + PAPER_BUILD_LOG.md) on top of
the machine-checkable source package. These tests pin the paper layer:
structure is read primarily from the ``.tex`` source, the extracted PDF text is
a confirming signal, thresholds are lenient (no style/word lock-in), and the
optional HTML presentation layer never gates.

The fast tests hand-craft minimal, valid, text-based PDFs (so the real
``pdftotext``/``pypdf`` extraction path runs without a TeX toolchain). One
integration test compiles genuine two-column LaTeX with ``pdflatex`` to prove
the pipeline is not merely fooling a text parser.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from argus_skill.verticals.physics import manuscript as ms
from tests.skills._physics_paper_fixtures import (
    MANUSCRIPT_PDF_LINES,
    make_pdf,
    write_complete_package,
    write_paper_layer,
    write_source_package,
)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _tex(*, n_cite: int = 8, n_eq: int = 4, n_table: int = 2, n_fig: int = 6,
         bib: bool = True, figs_at_end: bool = False) -> str:
    """A MANUSCRIPT.tex skeleton with tunable structure (never compiled here)."""
    cites = "".join("\\cite{r%d}" % i for i in range(1, n_cite + 1))
    eqs = "\n".join(
        "\\begin{equation}\\label{eq:%d} a = b \\end{equation}" % i for i in range(1, n_eq + 1)
    )
    tables = "\n".join(
        "\\begin{table}\\centering\\begin{tabular}{cc}a&b\\\\\\end{tabular}"
        "\\caption{T%d}\\label{tab:%d}\\end{table}" % (i, i)
        for i in range(1, n_table + 1)
    )
    figs = "\n".join(
        "\\begin{figure}\\centering\\rule{4cm}{3cm}\\caption{Figure %d.}\\label{fig:%d}\\end{figure}" % (i, i)
        for i in range(1, n_fig + 1)
    )
    bib_block = ""
    if bib:
        items = "\n".join("\\bibitem{r%d} Author %d (20%02d)." % (i, i, 10 + i) for i in range(1, 13))
        bib_block = "\\begin{thebibliography}{99}\n" + items + "\n\\end{thebibliography}\n"
    head = (
        "\\documentclass[twocolumn]{article}\n"
        "\\usepackage{amsmath,graphicx,booktabs}\n"
        "\\begin{document}\n\\title{T}\\maketitle\n"
        "\\section{Introduction} " + cites + "\n"
        "\\section{Model}\n" + eqs + "\nSee Eq.~\\eqref{eq:1}.\n" + tables + "\n"
    )
    tail = "\\section{Results} Table~\\ref{tab:1}.\n"
    if figs_at_end:
        return head + tail + bib_block + figs + "\n\\end{document}\n"
    return head + figs + "\n" + tail + bib_block + "\\end{document}\n"


@pytest.fixture()
def complete(tmp_path: Path) -> Path:
    write_complete_package(tmp_path)
    return tmp_path


def _paper_fails(root: Path) -> str:
    return " ".join(ms.verify_paper_style_deliverables(root))


# --------------------------------------------------------------------------- #
# the complete paper package passes                                            #
# --------------------------------------------------------------------------- #
def test_complete_paper_package_passes(complete: Path) -> None:
    assert ms.verify_paper_style_deliverables(complete) == []
    assert ms.verify_all_deliverables(complete) == []
    assert ms.main(["check", "--project-root", str(complete)]) == 0


# --------------------------------------------------------------------------- #
# missing paper deliverables                                                   #
# --------------------------------------------------------------------------- #
def test_markdown_only_package_fails(tmp_path: Path) -> None:
    write_source_package(tmp_path)  # source layer only — no .tex / .pdf
    fails = _paper_fails(tmp_path)
    assert "MANUSCRIPT.tex" in fails and "MANUSCRIPT.pdf" in fails and "SUPPLEMENT.pdf" in fails
    assert ms.verify_all_deliverables(tmp_path)  # aggregate contract fails


def test_missing_manuscript_pdf_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.pdf").unlink()
    assert "missing/empty MANUSCRIPT.pdf" in _paper_fails(complete)


def test_missing_supplement_pdf_fails(complete: Path) -> None:
    (complete / "SUPPLEMENT.pdf").unlink()
    assert "missing/empty SUPPLEMENT.pdf" in _paper_fails(complete)


def test_missing_manuscript_tex_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").unlink()
    assert "missing/empty MANUSCRIPT.tex" in _paper_fails(complete)


def test_pdf_no_extractable_text_fails_closed(complete: Path) -> None:
    make_pdf(complete / "MANUSCRIPT.pdf", [])  # valid, non-empty, but no text
    assert (complete / "MANUSCRIPT.pdf").stat().st_size > 0
    assert "no extractable text" in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# citations / bibliography (style-agnostic; read from the .tex)                #
# --------------------------------------------------------------------------- #
def test_missing_bibliography_mechanism_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").write_text(_tex(bib=False), encoding="utf-8")
    assert "no bibliography mechanism" in _paper_fails(complete)


def test_too_few_citations_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").write_text(_tex(n_cite=7), encoding="utf-8")
    assert "cite-family" in _paper_fails(complete)


def test_missing_references_section_in_pdf_fails(complete: Path) -> None:
    lines = [ln for ln in MANUSCRIPT_PDF_LINES if ln != "References"]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "no References/Bibliography section heading" in _paper_fails(complete)


def test_bibtex_key_leak_in_pdf_fails(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    lines.insert(2, "As shown in [Su1979Solitons], the effect persists.")
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "unresolved citation" in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# equations                                                                    #
# --------------------------------------------------------------------------- #
def test_too_few_equations_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").write_text(_tex(n_eq=3), encoding="utf-8")
    assert "display-equation environments" in _paper_fails(complete)


def test_too_few_equation_references_fails(complete: Path) -> None:
    lines = [ln for ln in MANUSCRIPT_PDF_LINES if "Eq. (" not in ln]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "equation numbers" in _paper_fails(complete)


def test_obvious_raw_math_fails(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    lines.insert(4, "The evolution operator is exp(-i H_2 tau_2) over one period and we sum_n over sites.")
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "un-rendered ASCII formula" in _paper_fails(complete)


def test_a_few_subscripts_do_not_fail(complete: Path) -> None:
    # A couple of stray ASCII subscripts (below the density threshold, no obvious
    # formula) must NOT block a legitimate paper.
    lines = list(MANUSCRIPT_PDF_LINES)
    lines.insert(4, "Here the fields H_1 and theta_v denote the two channels.")
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert ms.verify_paper_style_deliverables(complete) == []


# --------------------------------------------------------------------------- #
# tables                                                                        #
# --------------------------------------------------------------------------- #
def test_too_few_main_tables_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").write_text(_tex(n_table=1), encoding="utf-8")
    assert "table floats; need >= 2 main tables" in _paper_fails(complete)


def test_tables_not_referenced_fails(complete: Path) -> None:
    lines = [ln for ln in MANUSCRIPT_PDF_LINES if "Table" not in ln]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "distinct table" in _paper_fails(complete)


def test_too_few_supplementary_tables_fails(complete: Path) -> None:
    (complete / "SUPPLEMENT.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "\\section*{Supplementary Reproducibility}\\section*{Supplementary Methods}"
        "\\section*{Supplementary Claim audit}\n"
        "\\begin{table}\\begin{tabular}{cc}a&b\\\\\\end{tabular}\\end{table}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    assert "SUPPLEMENT.tex has 1 table floats" in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# figures                                                                       #
# --------------------------------------------------------------------------- #
def test_figure_not_cited_fails(complete: Path) -> None:
    lines = [
        ln if "Fig. 6" not in ln
        else "Fig. 3 and Fig. 4 present the diagnostics, and Fig. 5 shows the robustness."
        for ln in MANUSCRIPT_PDF_LINES
    ]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "does not cite figure(s) [6]" in _paper_fails(complete)


def test_figures_all_at_end_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.tex").write_text(_tex(figs_at_end=True), encoding="utf-8")
    assert "dumped at" in _paper_fails(complete)


def test_caption_over_word_cap_fails(complete: Path) -> None:
    long_caption = "Figure 1. " + ("word " * (ms.FIGURE_CAPTION_HARD_CAP + 20))
    (complete / "FIGURE_LEGENDS.md").write_text(long_caption + "\n", encoding="utf-8")
    fails = _paper_fails(complete)
    assert "figure 1 caption" in fails and "hard cap" in fails


# --------------------------------------------------------------------------- #
# forbidden strings (tiered)                                                    #
# --------------------------------------------------------------------------- #
def test_main_text_paths_fail(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    lines.insert(6, "See scripts/run.py and data/output.json for the pipeline.")
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    fails = _paper_fails(complete)
    assert "path/extension token" in fails


def test_availability_may_contain_file_paths_passes(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    idx = lines.index("Code availability. The analysis code is described in the Supplementary Information.")
    lines[idx] = "Code availability. See scripts/run.py, data/spectrum.json and results.csv in the archive."
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    # path/extension tokens are allowed inside the availability statement
    assert ms.verify_paper_style_deliverables(complete) == []


def test_tier_a_workflow_token_fails(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    lines.insert(6, "The Argus workspace produced these results.")
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    fails = _paper_fails(complete)
    assert "forbidden token" in fails and "argus" in fails.lower()


# --------------------------------------------------------------------------- #
# availability + supplement                                                     #
# --------------------------------------------------------------------------- #
def test_missing_code_availability_fails(complete: Path) -> None:
    lines = [ln for ln in MANUSCRIPT_PDF_LINES if not ln.startswith("Code availability")]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "Data availability and/or Code availability" in _paper_fails(complete)


def test_absolute_path_in_availability_fails(complete: Path) -> None:
    lines = list(MANUSCRIPT_PDF_LINES)
    idx = lines.index("Code availability. The analysis code is described in the Supplementary Information.")
    lines[idx] = "Code availability. Code lives at /home/user/project/run.py on the cluster."
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "absolute local path" in _paper_fails(complete)


def test_supplement_cited_too_few_fails(complete: Path) -> None:
    lines = [ln for ln in MANUSCRIPT_PDF_LINES if "Supplementary" not in ln]
    write_paper_layer(complete, manuscript_pdf_lines=lines)
    assert "cites the Supplement" in _paper_fails(complete)


def test_supplement_missing_claim_audit_fails(complete: Path) -> None:
    (complete / "SUPPLEMENT.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "\\section*{Supplementary Reproducibility}\\section*{Supplementary Methods}\n"
        "\\begin{table}\\begin{tabular}{cc}a&b\\\\\\end{tabular}\\end{table}\n"
        "\\begin{table}\\begin{tabular}{cc}c&d\\\\\\end{tabular}\\end{table}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    make_pdf(
        complete / "SUPPLEMENT.pdf",
        [
            "Supplementary Information",
            "Supplementary Reproducibility. Environment versions and seeds.",
            "Supplementary Methods. Numerical detail.",
            "Supplementary Table 1.",
            "Supplementary Table 2.",
        ],
    )
    assert "missing a 'Claim audit' section" in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# REVIEW.md paper-style audit                                                    #
# --------------------------------------------------------------------------- #
def test_review_missing_paper_audit_section_fails(complete: Path) -> None:
    (complete / "REVIEW.md").write_text("# Review\n## Physics Audit\nAll good.\n", encoding="utf-8")
    assert ms.PAPER_AUDIT_HEADING in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# section thickness floors (anti-thin only)                                     #
# --------------------------------------------------------------------------- #
def _md(*, intro_words: int, results_words: int) -> str:
    filler = "analysis bounded interpretation detail evidence result spectra "

    def words(n: int) -> str:
        return " ".join((filler * (n // 7 + 2)).split()[:n])

    return (
        "# A Model Study\n## Abstract\nx\n"
        "## Introduction\n" + words(intro_words) + "\n"
        "## Background and Related Work\nx\n## Model\nx\n## Methods\nx\n"
        "## Results\n" + words(results_words) + "\n"
        "## Discussion\nx\n## Limitations\nx\n## Conclusion\nx\n"
        "## References\nx\n## Data Availability\nx\n## Code Availability\nx\n"
    )


def test_thin_introduction_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.md").write_text(_md(intro_words=50, results_words=1300), encoding="utf-8")
    assert "Introduction is" in _paper_fails(complete)


def test_thin_results_fails(complete: Path) -> None:
    (complete / "MANUSCRIPT.md").write_text(_md(intro_words=700, results_words=50), encoding="utf-8")
    assert "Results is" in _paper_fails(complete)


# --------------------------------------------------------------------------- #
# --layer flag                                                                  #
# --------------------------------------------------------------------------- #
def test_layer_flag_source_and_paper(tmp_path: Path) -> None:
    write_source_package(tmp_path)  # source only
    assert ms.main(["check", "--project-root", str(tmp_path), "--layer", "source"]) == 0
    assert ms.main(["check", "--project-root", str(tmp_path), "--layer", "paper"]) == 1
    assert ms.main(["check", "--project-root", str(tmp_path), "--layer", "all"]) == 1
    write_paper_layer(tmp_path)
    assert ms.main(["check", "--project-root", str(tmp_path), "--layer", "all"]) == 0
    assert ms.main(["check", "--project-root", str(tmp_path), "--layer", "paper"]) == 0


# --------------------------------------------------------------------------- #
# integration: genuine LaTeX compiles and passes the full contract              #
# --------------------------------------------------------------------------- #
_REAL_MAIN_TEX = r"""\documentclass[twocolumn]{article}
\usepackage{amsmath,graphicx,booktabs}
\begin{document}
\title{A Diagnostic Study of a Model System}
\author{Anon}
\maketitle
\begin{abstract}
We report bounded, finite results for a model system.
\end{abstract}

\section{Introduction}
Prior work \cite{r1} and \cite{r2} established the setting \cite{r3}.

\section{Model}
\begin{equation}\label{eq:1} a = b. \end{equation}
\begin{equation}\label{eq:2} c = d. \end{equation}
\begin{align}\label{eq:3} e &= f. \end{align}
\begin{equation}\label{eq:4} g = h. \end{equation}
The relations Eq.~(\ref{eq:1}), Eq.~(\ref{eq:2}) and Eq.~(\ref{eq:3}) define the model \cite{r4}.
\begin{figure}\centering\rule{4cm}{3cm}\caption{First figure.}\label{fig:1}\end{figure}
\begin{table}\centering\begin{tabular}{cc}\toprule a&b\\\midrule 1&2\\\bottomrule\end{tabular}\caption{Parameters.}\label{tab:1}\end{table}

\section{Methods}
We compute the spectra \cite{r5}. See Fig.~\ref{fig:1} and Table~\ref{tab:1}.
\begin{figure}\centering\rule{4cm}{3cm}\caption{Second figure.}\label{fig:2}\end{figure}
\begin{table}\centering\begin{tabular}{cc}\toprule c&d\\\midrule 3&4\\\bottomrule\end{tabular}\caption{Design.}\label{tab:2}\end{table}

\section{Results}
Fig.~\ref{fig:2}, Fig.~\ref{fig:3}, Fig.~\ref{fig:4}, Fig.~\ref{fig:5} and Fig.~\ref{fig:6} show the trends; see Table~\ref{tab:2} \cite{r6} \cite{r9}.
Additional detail is in Supplementary Methods and Supplementary Table~1; see Supplementary Section~2.
\begin{figure}\centering\rule{4cm}{3cm}\caption{Third figure.}\label{fig:3}\end{figure}
\begin{figure}\centering\rule{4cm}{3cm}\caption{Fourth figure.}\label{fig:4}\end{figure}
\begin{figure}\centering\rule{4cm}{3cm}\caption{Fifth figure.}\label{fig:5}\end{figure}
\begin{figure}\centering\rule{4cm}{3cm}\caption{Sixth figure.}\label{fig:6}\end{figure}

\section{Discussion}
The findings are consistent with prior work \cite{r7} within the tested range.

\section{Limitations}
The regime is finite and the sampling is bounded.

\section{Conclusion}
We summarise bounded contributions \cite{r8}.

\section*{Data availability}
Processed data tables are provided in the Supplementary Information.

\section*{Code availability}
The analysis code is described in the Supplementary Information.

\begin{thebibliography}{99}
\bibitem{r1} A. Author, Title one (2011).
\bibitem{r2} B. Author, Title two (2012).
\bibitem{r3} C. Author, Title three (2013).
\bibitem{r4} D. Author, Title four (2014).
\bibitem{r5} E. Author, Title five (2015).
\bibitem{r6} F. Author, Title six (2016).
\bibitem{r7} G. Author, Title seven (2017).
\bibitem{r8} H. Author, Title eight (2018).
\bibitem{r9} I. Author, Title nine (2019).
\bibitem{r10} J. Author, Title ten (2020).
\bibitem{r11} K. Author, Title eleven (2021).
\bibitem{r12} L. Author, Title twelve (2022).
\end{thebibliography}
\end{document}
"""

_REAL_SUPP_TEX = r"""\documentclass{article}
\usepackage{booktabs}
\begin{document}
\section*{Supplementary Information}
This supplement provides reproducibility, methods detail, and the claim audit.
\section*{Supplementary Reproducibility}
Environment versions, random seeds, and commands.
\section*{Supplementary Methods}
Numerical detail and derivations.
\section*{Supplementary Claim audit}
An evidence ledger mapping each claim to its evidence.
\begin{table}\centering\begin{tabular}{cc}\toprule a&b\\\bottomrule\end{tabular}\caption{S1.}\end{table}
\begin{table}\centering\begin{tabular}{cc}\toprule c&d\\\bottomrule\end{tabular}\caption{S2.}\end{table}
\end{document}
"""


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_real_latex_compiles_and_passes(tmp_path: Path) -> None:
    write_source_package(tmp_path)
    (tmp_path / "MANUSCRIPT.tex").write_text(_REAL_MAIN_TEX, encoding="utf-8")
    (tmp_path / "SUPPLEMENT.tex").write_text(_REAL_SUPP_TEX, encoding="utf-8")
    (tmp_path / "PAPER_BUILD_LOG.md").write_text(
        "engine: pdflatex; passes: 2; warnings: none; TeX Live 2023\n", encoding="utf-8"
    )
    pdflatex = shutil.which("pdflatex")
    assert pdflatex is not None
    for tex in ("MANUSCRIPT.tex", "SUPPLEMENT.tex"):
        result = None
        for _ in range(2):  # two passes resolve \ref / \cite numbers
            result = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tex],
                cwd=tmp_path, capture_output=True, text=True, timeout=180,
            )
        pdf = tmp_path / tex.replace(".tex", ".pdf")
        assert pdf.exists() and pdf.stat().st_size > 0, (result.stdout[-2000:] if result else "")

    # a genuinely compiled paper satisfies the full, real contract
    assert ms.verify_all_deliverables(tmp_path) == [], ms.verify_all_deliverables(tmp_path)
