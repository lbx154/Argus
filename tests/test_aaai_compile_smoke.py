"""Real LaTeX compile smoke for the AAAI-2026 venue contract.

Builds a minimal AAAI paper following the exact preamble the AAAI Paper Drafting
skill prescribes, compiles it with the official aaai2026.sty/.bst, and asserts:

* it produces a PDF (the contract is actually compilable).

Skips cleanly when pdflatex or the AAAI kit is unavailable, so it is portable to
CI without the (non-redistributable) style files.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


_KIT_CANDIDATES = [
    os.environ.get("ARGUS_SKILL_AAAI_KIT", ""),
    "/tmp/aaaikit",
    str(Path.home() / "AI-Research-SKILLs/20-ml-paper-writing/ml-paper-writing/templates/aaai2026"),
]

# Minimal AAAI paper following the AAAI Paper Drafting skill's contract:
# documentclass[letterpaper], usepackage[submission]{aaai2026}, times/helvet/
# courier, \pdfinfo, \setcounter{secnumdepth}{0}, and NO \bibliographystyle.
_MAIN_TEX = r"""\documentclass[letterpaper]{article}
\usepackage[submission]{aaai2026}
\usepackage{times}
\usepackage{helvet}
\usepackage{courier}
\usepackage[hyphens]{url}
\usepackage{graphicx}
\usepackage{natbib}
\usepackage{caption}
\setcounter{secnumdepth}{0}
\pdfinfo{
/TemplateVersion (2026.1)
}
\title{A Minimal AAAI Smoke Paper}
\author{}
\begin{document}
\maketitle
\begin{abstract}
A minimal compile smoke test for the AAAI 2026 venue contract.
\end{abstract}
\section{Introduction}
We cite prior work \citep{smith2024}.
\section{Conclusion}
Done.
\bibliography{aaai2026}
\section*{Reproducibility Checklist}
This paper includes a reproducibility checklist.
\end{document}
"""

_BIB = (
    "@inproceedings{smith2024,title={A Study},"
    "author={Smith, Jane},booktitle={AAAI},year={2024}}\n"
)


def _find_kit() -> Path | None:
    for cand in _KIT_CANDIDATES:
        if not cand:
            continue
        p = Path(cand)
        if (p / "aaai2026.sty").is_file() and (p / "aaai2026.bst").is_file():
            return p
    return None


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("pdflatex") is None or _find_kit() is None,
        reason="pdflatex and/or the AAAI 2026 kit (aaai2026.sty/.bst) are unavailable",
    ),
]


def test_minimal_aaai_paper_compiles(tmp_path: Path) -> None:
    kit = _find_kit()
    assert kit is not None  # guarded by skipif
    paper = tmp_path / "paper"
    paper.mkdir()
    shutil.copy(kit / "aaai2026.sty", paper / "aaai2026.sty")
    shutil.copy(kit / "aaai2026.bst", paper / "aaai2026.bst")
    (paper / "aaai2026.bib").write_text(_BIB, encoding="utf-8")
    (paper / "main.tex").write_text(_MAIN_TEX, encoding="utf-8")

    def _run(cmd: list[str]) -> None:
        subprocess.run(
            cmd, cwd=paper, capture_output=True, text=True, timeout=120, check=False
        )

    _run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
    _run(["bibtex", "main"])
    _run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
    _run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])

    # The contract is actually compilable.
    assert (paper / "main.pdf").is_file(), "AAAI minimal paper did not produce a PDF"
