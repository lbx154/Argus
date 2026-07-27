from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "technical_report"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _all_report_source() -> str:
    return "\n".join(
        [
            _read("technical_report/main.tex"),
            *[
                path.read_text(encoding="utf-8")
                for path in sorted((REPORT / "sections").glob("*.tex"))
            ],
        ]
    )


CURRENT_SECTION_INPUTS = (
    "01_introduction",
    "02_related_work",
    "03_problem_formulation",
    "04_argus_method",
    "05_empirical_methodology",
    "06_results",
    "06b_vertical_trace",
    "06c_paper_production_case_study",
    "07_discussion",
    "08_limitations",
    "09_conclusion",
)


def test_report_is_the_current_academic_paper() -> None:
    main = _read("technical_report/main.tex")

    assert "A General-Purpose Agentic Runtime for Long-Horizon Reasoning" in main
    assert "Technical Report 0.3" not in main
    assert (REPORT / "argus-technical-report.pdf").stat().st_size > 100_000


def test_current_report_palette_and_cover_assets_are_wired() -> None:
    main = _read("technical_report/main.tex")

    for color in (
        r"\definecolor{argusblue}{HTML}{315BCE}",
        r"\definecolor{argusdeep}{HTML}{24465D}",
        r"\definecolor{papercream}{HTML}{FBF7EE}",
        r"\definecolor{papergold}{HTML}{D9C58F}",
    ):
        assert color in main
    assert "figures/argus_teaser.pdf" in main


def test_current_academic_sections_are_complete_and_ordered() -> None:
    main = _read("technical_report/main.tex")
    inputs = re.findall(r"\\input\{sections/([^}]+)\}", main)

    assert tuple(inputs[: len(CURRENT_SECTION_INPUTS)]) == CURRENT_SECTION_INPUTS
    assert inputs[-1] == "90_appendix"
    for section in CURRENT_SECTION_INPUTS:
        assert (REPORT / "sections" / f"{section}.tex").is_file()


def test_report_states_the_current_role_and_persistence_contract() -> None:
    source = _all_report_source()

    for required in (
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "CHECKPOINT.md",
        "fixed-model runtime",
        "persistent campaign state",
    ):
        assert required in source
    assert "final artifacts alone" in source


def test_report_keeps_empirical_claims_bounded() -> None:
    source = _all_report_source()

    for value in (r"78\%", r"59\%", r"1.41$\times$", "34 verifier recoveries"):
        assert value in source
    for limitation in (
        "whole-system behavior",
        "rather than combined into a universal score",
        "not paper acceptance",
    ):
        assert limitation in source


def test_no_banned_rhetoric_in_current_report() -> None:
    source = _all_report_source()

    assert not re.search(
        r"\bdumb pipe\b|\bplumbing\b|not smarter than",
        source,
        flags=re.IGNORECASE,
    )
