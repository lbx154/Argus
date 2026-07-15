from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "technical_report"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_report_identity_is_dense_intelligence_03() -> None:
    main = _read("technical_report/main.tex")

    assert "Technical Report 0.3" in main
    assert "Dense Intelligence for an Expanding Research Frontier" in main
    assert "Technical Report 0.2" not in main


def test_cover_is_light_blue_gold() -> None:
    main = _read("technical_report/main.tex")

    assert r"\definecolor{systemblue}{HTML}{315BCE}" in main
    assert r"\definecolor{deepblue}{HTML}{214884}" in main
    assert r"\definecolor{frontiergold}{HTML}{C38A20}" in main
    assert r"\pagecolor{bonewhite}" in main
    assert "Dark cover" not in main


def test_act_one_sections_and_master_spine_are_wired() -> None:
    main = _read("technical_report/main.tex")

    assert r"\input{sections/01_executive_thesis}" in main
    assert r"\input{sections/02_dense_intelligence}" in main
    assert r"\input{sections/03_episodic_agents}" in main
    thesis = _read("technical_report/sections/01_executive_thesis.tex")
    assert r"\includegraphics" in thesis
    assert "master_spine.pdf" in thesis
    assert "Every run expands the frontier." in thesis


def test_dense_intelligence_not_presented_as_measured_score() -> None:
    dense = _read("technical_report/sections/02_dense_intelligence.tex")

    assert r"\rho_{\mathrm{DI}}(T)" in dense
    assert "explanatory construct" in dense
    assert "not a reported benchmark metric" in dense
    assert "universal superiority" not in dense


def test_no_banned_rhetoric_in_report_source() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPORT / "sections").glob("*.tex"))
    )
    assert len(re.findall(r"\bguardrails?\b", source, flags=re.IGNORECASE)) <= 2
    assert not re.search(r"\bdumb pipe\b|\bplumbing\b|not smarter than", source, flags=re.IGNORECASE)
