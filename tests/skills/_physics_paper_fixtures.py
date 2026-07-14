"""Shared fixtures for the physics manuscript + paper-style contract tests.

Not a test module (no ``test_`` prefix), so pytest does not collect it. It
builds a project that satisfies BOTH delivery layers the manuscript stage
gates:

* the machine-checkable **source layer** (MANUSCRIPT.md, figures, legends,
  CLAIMS.csv, references, reproducibility, methods, REVIEW.md), and
* the LaTeX-compiled **paper layer** (MANUSCRIPT.tex/pdf, SUPPLEMENT.tex/pdf,
  PAPER_BUILD_LOG.md).

``make_pdf`` hand-crafts a minimal but valid text-based PDF so the real
extraction path (``pdftotext`` / ``pypdf``) is exercised without a LaTeX
toolchain. The integration test compiles genuine LaTeX separately.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.verticals.physics import manuscript as ms


# --------------------------------------------------------------------------- #
# A minimal, valid, text-based PDF                                             #
# --------------------------------------------------------------------------- #
def make_pdf(path: Path, lines: list[str]) -> None:
    """Write a valid single-page PDF whose visible text is ``lines`` (one per
    row). With ``lines == []`` the PDF has no text (for fail-closed tests)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 10 Tf", "1 0 0 1 56 760 Tm", "12 TL"]
    for ln in lines:
        content_lines.append(f"({esc(ln)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", "replace")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n" % n
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos)
    path.write_bytes(out)


# --------------------------------------------------------------------------- #
# Default PDF text bodies (satisfy every paper-layer PDF check)                #
# --------------------------------------------------------------------------- #
#: MANUSCRIPT.pdf text — Fig. 1..6, Table 1/2, Eq. (1)..(3), Data/Code
#: availability, three Supplementary cross-references, References last.
MANUSCRIPT_PDF_LINES: list[str] = [
    "A Diagnostic Study of a Model System",
    "Abstract. We investigate a model system and report bounded, finite results.",
    "1. Introduction. Prior work [1, 2] established the background and open questions.",
    "2. Model. The governing relations are stated in Eq. (1) and Eq. (2), and the",
    "observable follows from Eq. (3). Parameters are listed in Table 1.",
    "3. Methods. We compute the spectra; the design is summarised in Table 2 [3].",
    "4. Results. Figure 1 and Fig. 2 show the main trends across the tested range.",
    "Fig. 3 and Fig. 4 present the diagnostics, and Fig. 5 and Fig. 6 the robustness.",
    "Extended derivations appear in Supplementary Methods and Supplementary Table 1.",
    "See Supplementary Section 2 for the full parameter sweep [4, 5].",
    "5. Discussion. The findings are consistent with prior work [6] within the range.",
    "6. Limitations. The tested regime is finite and the sampling is bounded.",
    "7. Conclusion. We summarise bounded, evidence-limited contributions.",
    "Data availability. Processed tables accompany the Supplementary Information.",
    "Code availability. The analysis code is described in the Supplementary Information.",
    "References",
    "[1] A. Author, A model study, Journal of Physics (2019).",
    "[2] B. Author, Related methods, Physical Review (2020).",
    "[3] C. Author, Numerical diagnostics, Computational Physics (2021).",
]

#: SUPPLEMENT.pdf text — reproducibility, claim-audit and methods content.
SUPPLEMENT_PDF_LINES: list[str] = [
    "Supplementary Information",
    "This supplement collects reproducibility, methods detail and the claim audit.",
    "Supplementary Reproducibility. Environment versions, seeds and commands.",
    "Supplementary Methods. Numerical detail and extended derivations.",
    "Supplementary Claim audit. Evidence ledger mapping each claim to its evidence.",
    "Supplementary Table 1. Full parameter set.",
    "Supplementary Table 2. Robustness summary.",
]


# --------------------------------------------------------------------------- #
# LaTeX source skeletons (structure only; the fast tests do not compile them)  #
# --------------------------------------------------------------------------- #
def _manuscript_tex() -> str:
    bib = "\n".join(f"\\bibitem{{r{i}}} Author {i}, Title {i} (20{10 + i})." for i in range(1, 13))
    cites = "".join(f"\\cite{{r{i}}}" for i in range(1, 9))  # 8 citation call sites
    figs_early = "\n".join(
        f"\\begin{{figure}}\\centering\\rule{{4cm}}{{3cm}}\\caption{{Figure {i}.}}\\label{{fig:{i}}}\\end{{figure}}"
        for i in range(1, 4)
    )
    figs_mid = "\n".join(
        f"\\begin{{figure}}\\centering\\rule{{4cm}}{{3cm}}\\caption{{Figure {i}.}}\\label{{fig:{i}}}\\end{{figure}}"
        for i in range(4, 7)
    )
    return (
        "\\documentclass[twocolumn]{article}\n"
        "\\usepackage{amsmath,graphicx,booktabs}\n"
        "\\begin{document}\n"
        "\\title{A Diagnostic Study of a Model System}\\maketitle\n"
        "\\begin{abstract}We report bounded results.\\end{abstract}\n"
        "\\section{Introduction} Background " + cites + ".\n"
        "\\section{Model}\n"
        "\\begin{equation}\\label{eq:1} a = b \\end{equation}\n"
        "\\begin{equation}\\label{eq:2} c = d \\end{equation}\n"
        "\\begin{align}\\label{eq:3} e &= f \\end{align}\n"
        "\\begin{equation}\\label{eq:4} g = h \\end{equation}\n"
        "See Eq.~\\eqref{eq:1}.\n"
        + figs_early + "\n"
        "\\begin{table}\\centering\\begin{tabular}{cc}a&b\\\\\\end{tabular}"
        "\\caption{Parameters.}\\label{tab:1}\\end{table}\n"
        "\\section{Methods} Fig.~\\ref{fig:1}.\n"
        "\\begin{table}\\centering\\begin{tabular}{cc}c&d\\\\\\end{tabular}"
        "\\caption{Design.}\\label{tab:2}\\end{table}\n"
        "\\section{Results} Table~\\ref{tab:1}.\n"
        + figs_mid + "\n"
        "\\begin{thebibliography}{99}\n" + bib + "\n\\end{thebibliography}\n"
        "\\end{document}\n"
    )


def _supplement_tex() -> str:
    return (
        "\\documentclass{article}\n\\usepackage{booktabs}\n\\begin{document}\n"
        "\\section*{Supplementary Information}\n"
        "\\section*{Supplementary Reproducibility}\n"
        "\\section*{Supplementary Methods}\n"
        "\\section*{Supplementary Claim audit}\n"
        "\\begin{table}\\centering\\begin{tabular}{cc}a&b\\\\\\end{tabular}"
        "\\caption{S1.}\\end{table}\n"
        "\\begin{table}\\centering\\begin{tabular}{cc}c&d\\\\\\end{tabular}"
        "\\caption{S2.}\\end{table}\n"
        "\\end{document}\n"
    )


def _lorem(n_words: int) -> str:
    sentence = "This section presents the analysis and bounded interpretation in detail "
    words = (sentence * ((n_words // len(sentence.split())) + 2)).split()
    return " ".join(words[:n_words])


def _manuscript_md() -> str:
    # Introduction >= 600 words, Results >= 1200 words; the Data/Code Availability
    # lines are kept verbatim so the existing removal test still matches.
    return (
        "# A Diagnostic Study of a Model System\n"
        "## Abstract\nWe report bounded, finite, evidence-limited results.\n"
        "## Introduction\n" + _lorem(700) + "\n"
        "## Background and Related Work\nPrior work is summarised here.\n"
        "## Model\nThe governing relations and observables are defined.\n"
        "## Methods\nWe compute the spectra with stated tolerances.\n"
        "## Results\n" + _lorem(1300) + "\n"
        "## Discussion\nThe findings are consistent within the tested range.\n"
        "## Limitations\nNumerical, finite-size and generalization limits apply.\n"
        "## Conclusion\nWe summarise bounded contributions.\n"
        "## References\nSee REFERENCES.bib.\n"
        "## Data Availability\nx\n## Code Availability\nx\n"
    )


def _review_md() -> str:
    return (
        "# Review\n"
        "## Physics Audit\nThe claims are bounded and evidence-linked.\n"
        f"## {ms.PAPER_AUDIT_HEADING}\n"
        "MANUSCRIPT.pdf and SUPPLEMENT.pdf present; citations, equations, tables, "
        "figures, availability and Supplement cross-references verified; no overclaim.\n"
    )


# --------------------------------------------------------------------------- #
# Package builders                                                             #
# --------------------------------------------------------------------------- #
def write_source_package(root: Path) -> None:
    """Write the machine-checkable source layer (no paper layer)."""
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        '{"vertical": "physics", "current_stage": "manuscript"}', encoding="utf-8"
    )
    (root / "MANUSCRIPT.md").write_text(_manuscript_md(), encoding="utf-8")
    figs = root / "figures"
    figs.mkdir(exist_ok=True)
    for i in range(1, 7):
        (figs / f"fig{i}_panel.png").write_text("PNG", encoding="utf-8")
    (root / "FIGURE_LEGENDS.md").write_text(
        "\n".join(
            f"Figure {i}. A concise legend describing panel meaning, axes and units."
            for i in range(1, 7)
        ) + "\n",
        encoding="utf-8",
    )
    (root / "REFERENCES.bib").write_text(
        "\n".join(f"@article{{ref{i}, title={{T{i}}}, year={{2024}}}}" for i in range(8)),
        encoding="utf-8",
    )
    (root / "CLAIMS.csv").write_text(
        ",".join(ms.CLAIMS_COLUMNS) + "\n"
        "C1,the model is consistent,theory,equation,eq:1,supported,linear regime,ok\n",
        encoding="utf-8",
    )
    (root / "REPRODUCIBILITY.md").write_text("commands, versions, seeds ...\n", encoding="utf-8")
    (root / "METHODS_DETAIL.md").write_text("derivations, pseudocode ...\n", encoding="utf-8")
    (root / "REVIEW.md").write_text(_review_md(), encoding="utf-8")
    # optional presentation layer (never gates)
    (root / "HTML_DEMO").mkdir(exist_ok=True)
    (root / "HTML_DEMO" / "index.html").write_text("<html>demo</html>", encoding="utf-8")


def write_paper_layer(
    root: Path,
    *,
    manuscript_pdf_lines: list[str] | None = None,
    supplement_pdf_lines: list[str] | None = None,
) -> None:
    """Write the LaTeX paper layer (hand-crafted, extractable PDFs)."""
    (root / "MANUSCRIPT.tex").write_text(_manuscript_tex(), encoding="utf-8")
    (root / "SUPPLEMENT.tex").write_text(_supplement_tex(), encoding="utf-8")
    (root / "PAPER_BUILD_LOG.md").write_text(
        "engine: pdflatex; passes: 2; warnings: none; TeX Live 2023\n", encoding="utf-8"
    )
    make_pdf(root / "MANUSCRIPT.pdf", manuscript_pdf_lines if manuscript_pdf_lines is not None else MANUSCRIPT_PDF_LINES)
    make_pdf(root / "SUPPLEMENT.pdf", supplement_pdf_lines if supplement_pdf_lines is not None else SUPPLEMENT_PDF_LINES)


def write_complete_package(root: Path) -> None:
    """Both layers — satisfies verify_all_deliverables()."""
    write_source_package(root)
    write_paper_layer(root)
