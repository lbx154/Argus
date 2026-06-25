"""AAAI-only structural compliance checks in paper_structural_minimums.

These activate only when the resolved venue is AAAI (target_venue=AAAI). They
must NEVER fire for the default EMNLP venue. They encode the AAAI-2026 LaTeX
contract verified against aaai2026.sty: \\pdfinfo required, aaai2026 style
package required, NO manual \\bibliographystyle (the class sets it), and
hyperref/navigator forbidden.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.paper_structural_minimums import (
    validate_paper_structural_minimums,
)

_AAAI_PREAMBLE = (
    "\\documentclass[letterpaper]{article}\n"
    "\\usepackage[submission]{aaai2026}\n"
    "\\usepackage{times}\n\\usepackage{helvet}\n\\usepackage{courier}\n"
    "\\pdfinfo{/TemplateVersion (2026.1)}\n"
)


def _mk(tmp_path: Path, venue: str, body: str) -> set[str]:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "paper").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"target_venue": venue}), encoding="utf-8"
    )
    (tmp_path / "paper" / "main.tex").write_text(body, encoding="utf-8")
    report = validate_paper_structural_minimums(tmp_path)
    return {i.code for i in report.issues}


_AAAI_CODES = {
    "missing_pdfinfo_block",
    "missing_aaai_style_package",
    "forbidden_bibliographystyle",
    "forbidden_package_present",
    "uses_nocopyright",
    "missing_reproducibility_checklist",
}


def test_aaai_flags_missing_pdfinfo_and_style(tmp_path: Path) -> None:
    codes = _mk(tmp_path, "AAAI", "\\documentclass{article}\n\\section{Intro}\n")
    assert "missing_pdfinfo_block" in codes
    assert "missing_aaai_style_package" in codes


def test_aaai_forbids_manual_bibliographystyle(tmp_path: Path) -> None:
    body = _AAAI_PREAMBLE + "\\bibliographystyle{aaai2026}\n\\section{Intro}\n"
    codes = _mk(tmp_path, "AAAI", body)
    assert "forbidden_bibliographystyle" in codes


def test_aaai_forbids_hyperref(tmp_path: Path) -> None:
    body = _AAAI_PREAMBLE + "\\usepackage{hyperref}\n\\section{Intro}\n"
    codes = _mk(tmp_path, "AAAI", body)
    assert "forbidden_package_present" in codes


def test_aaai_forbids_nocopyright(tmp_path: Path) -> None:
    body = _AAAI_PREAMBLE + "\\nocopyright\n\\section{Intro}\n"
    codes = _mk(tmp_path, "AAAI", body)
    assert "uses_nocopyright" in codes


def test_aaai_requires_reproducibility_checklist(tmp_path: Path) -> None:
    # Compliant preamble but no checklist section -> flagged.
    codes = _mk(tmp_path, "AAAI", _AAAI_PREAMBLE + "\\section{Intro}\n")
    assert "missing_reproducibility_checklist" in codes
    # With the checklist section -> not flagged.
    with_checklist = _AAAI_PREAMBLE + "\\section{Intro}\n\\section*{Reproducibility Checklist}\nfoo\n"
    assert "missing_reproducibility_checklist" not in _mk(tmp_path / "b", "AAAI", with_checklist)


def test_emnlp_never_sees_aaai_codes(tmp_path: Path) -> None:
    # The same non-compliant body must NOT trigger any AAAI code for EMNLP.
    body = "\\documentclass{article}\n\\usepackage{hyperref}\n\\bibliographystyle{acl_natbib}\n\\nocopyright\n\\section{Intro}\n"
    codes = _mk(tmp_path, "EMNLP", body)
    assert not (codes & _AAAI_CODES)
