"""Tests for argus_skill.tools.format_facts (structured PDF format
extractor used by the exemplar_grounding gate)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.tools.format_facts import (
    DEFAULT_TOLERANCES,
    diff_against_exemplar,
    extract_format_facts,
)
# Re-use pdf_chat's PDF builder so we test against real (toy) PDFs, not
# golden fixtures.
from tests.tools.test_pdf_chat import _build_pdf  # type: ignore


@pytest.fixture
def toy_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "toy.pdf"
    _build_pdf(pdf, pages=[
        "Abstract\n"
        "Short abstract of the paper. We propose a method.\n",
        "1 Introduction\n"
        "Background. See Figure 1 and Figure 2 and Figure 3.\n"
        "Prior work (Smith et al., 2024; Lee, 2023) and [12] discussed this.\n"
        "Also see Table 1.\n",
        "2 Related Work\n"
        "We discuss prior work in detail across multiple paragraphs.\n"
        "More citations (Jones, 2022; Patel et al., 2023).\n",
        "3 Method\n"
        "We propose a model. See Table 2.\n",
        "4 Experiments\n"
        "Results in Figure 4. Cited (Brown, 2024).\n",
        "5 Conclusion\n"
        "We conclude. Future work (Wang, 2023).\n",
        "References\n"
        "[1] Smith et al. 2024.\n"
        "[2] Lee 2023.\n",
    ])
    return pdf


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extract_total_pages(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    assert facts.total_pages == 7


def test_extract_sections(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    # We expect: Abstract, Introduction, Related Work, Method, Experiments,
    # Conclusion, References. 7 section titles.
    titles_lower = " ".join(facts.section_titles).lower()
    for keyword in ("abstract", "introduction", "related work",
                    "method", "experiments", "conclusion", "references"):
        assert keyword in titles_lower
    assert facts.section_count >= 6


def test_extract_figures_and_tables(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    # Toy paper references Figure 1, 2, 3, 4 → count=4 max=4
    assert facts.figure_count == 4
    assert facts.figure_max_index == 4
    # Table 1, Table 2 → count=2 max=2
    assert facts.table_count == 2


def test_extract_citations(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    # Several author-year citations in parens + one numeric [12]
    assert facts.citation_count >= 5
    assert facts.citations_per_page > 0


def test_extract_section_chars(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    assert facts.abstract_chars > 0
    assert facts.intro_chars > 0
    assert facts.related_work_chars > 0
    assert facts.conclusion_chars > 0


def test_extract_references_page(toy_pdf: Path) -> None:
    facts = extract_format_facts(toy_pdf)
    # References is on page 7 of the 7-page toy
    assert facts.references_page == 7
    assert facts.body_pages_before_references == 6


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def test_diff_within_tolerance_passes() -> None:
    paper = {"total_pages": 8, "figure_count": 3, "citations_per_page": 5.0}
    exemplar = {"total_pages": 7, "figure_count": 4, "citations_per_page": 4.5}
    findings = diff_against_exemplar(paper, exemplar)
    by_field = {f.field: f for f in findings}
    assert by_field["total_pages"].within_tolerance
    assert by_field["figure_count"].within_tolerance
    assert by_field["citations_per_page"].within_tolerance


def test_diff_huge_delta_fails_tolerance() -> None:
    paper = {"total_pages": 1, "figure_count": 0, "citations_per_page": 0}
    exemplar = {"total_pages": 8, "figure_count": 5, "citations_per_page": 8.0}
    findings = diff_against_exemplar(paper, exemplar)
    by_field = {f.field: f for f in findings}
    assert not by_field["total_pages"].within_tolerance
    assert not by_field["figure_count"].within_tolerance
    assert not by_field["citations_per_page"].within_tolerance


def test_diff_skips_missing_fields() -> None:
    """Either side missing a key → that field is skipped, not failed."""
    paper = {"total_pages": 5}
    exemplar = {"figure_count": 3}
    findings = diff_against_exemplar(paper, exemplar)
    # No field common to both → empty findings
    assert findings == []


def test_diff_tolerances_default_set_is_complete() -> None:
    expected = {
        "total_pages", "section_count", "figure_count", "table_count",
        "citations_per_page", "body_pages_before_references",
    }
    assert set(DEFAULT_TOLERANCES) == expected
    for limits in DEFAULT_TOLERANCES.values():
        assert "abs" in limits and "rel" in limits


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_json_emits_facts(toy_pdf: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.format_facts",
         str(toy_pdf), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["total_pages"] == 7
    assert data["figure_count"] == 4


def test_cli_write_creates_sidecar(toy_pdf: Path, tmp_path: Path) -> None:
    sidecar = tmp_path / "facts.json"
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.format_facts",
         str(toy_pdf), "--write", str(sidecar)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["section_count"] >= 6


def test_cli_missing_pdf_errors() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.format_facts",
         "/tmp/__nope__.pdf"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "not found" in proc.stderr.lower()
