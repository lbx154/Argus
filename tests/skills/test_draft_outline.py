"""Tests for ``argus_skill.skills.draft_outline``."""
from __future__ import annotations

from pathlib import Path

from argus_skill.skills.draft_outline import (
    cross_check_figure_ids,
    load_outline,
    parse_outline,
    validate_outline,
)

_GOOD_OUTLINE = """\
---
outline_version: 1
mission_sha: deadbeef
---

## Sections
- title: Introduction
  goal: motivate trap-vs-control framing
- title: Method
  goal: describe taxonomy + verifier
- title: Results
  goal: present main matrix and gap analysis
- title: Discussion
  goal: limitations and threats to validity

## Figures
- id: F1_teaser
  style_ref: MMMU2024 Fig.1
  data_source: bench/dev_smoke/items.jsonl
  caption_placeholder: trap vs control example
- id: F2_results_heatmap
  style_ref: MathVista2024 Tab.2
  data_source: paper/artifacts/results_table.tsv
  caption_placeholder: model x trap-family accuracy
- id: F3_severity_ladder
  style_ref: HallusionBench2024 Fig.3
  data_source: paper/artifacts/severity_table.tsv
  caption_placeholder: accuracy vs severity

## Experiments
- id: E1_main_matrix
  cell_spec: 13 models x 5 trap families x 3 seeds
  expected_metric: trap-control gap
  n_seeds: 3
- id: E2_severity
  cell_spec: same models x severity 1-3
  expected_metric: monotonicity
  n_seeds: 3
"""


def test_parse_good_outline() -> None:
    o = parse_outline(_GOOD_OUTLINE)
    assert o.frontmatter["outline_version"] == "1"
    assert len(o.sections) == 4
    assert len(o.figures) == 3
    assert len(o.experiments) == 2
    assert "F1_teaser" in o.figure_ids()
    assert o.figures[0].style_ref == "MMMU2024 Fig.1"
    assert o.experiments[0].n_seeds == 3


def test_validate_good_outline_no_blockers() -> None:
    o = parse_outline(_GOOD_OUTLINE)
    issues = validate_outline(o)
    blockers = [i for i in issues if i.severity in ("missing", "unfilled")]
    assert blockers == []


def test_validate_missing_outline() -> None:
    issues = validate_outline(None)
    assert any(i.code == "outline_missing" for i in issues)
    assert all(i.severity == "missing" for i in issues if i.code == "outline_missing")


def test_validate_underfilled_figures() -> None:
    text = """\
---
outline_version: 1
---

## Sections
- title: Intro
  goal: x
- title: Method
  goal: y
- title: Results
  goal: z
- title: Discussion
  goal: w

## Figures
- id: F1
  style_ref: ref
  data_source: src
  caption_placeholder: cap

## Experiments
- id: E1
  cell_spec: spec
  expected_metric: m
"""
    o = parse_outline(text)
    issues = validate_outline(o)
    assert any(i.code == "figures_underfilled" for i in issues)


def test_validate_figure_field_missing() -> None:
    text = """\
---
outline_version: 1
---

## Sections
- title: A
  goal: g
- title: B
  goal: g
- title: C
  goal: g
- title: D
  goal: g

## Figures
- id: F1
  style_ref: ref
- id: F2
  style_ref: ref
  data_source: src
  caption_placeholder: cap
- id: F3
  style_ref: ref
  data_source: src
  caption_placeholder: cap

## Experiments
- id: E1
  cell_spec: s
  expected_metric: m
"""
    o = parse_outline(text)
    issues = validate_outline(o)
    field_issues = [i for i in issues if i.code == "figure_field_missing"]
    assert any("F1" in i.message for i in field_issues)


def test_validate_duplicate_figure_id() -> None:
    text = _GOOD_OUTLINE.replace("F2_results_heatmap", "F1_teaser")
    o = parse_outline(text)
    issues = validate_outline(o)
    assert any(i.code == "figure_id_duplicate" for i in issues)


def test_cross_check_orphan() -> None:
    o = parse_outline(_GOOD_OUTLINE)
    # main.tex has fig:F1_teaser (matches), fig:rogue (orphan)
    issues = cross_check_figure_ids(o, ["F1_teaser", "rogue"])
    assert len(issues) == 1
    assert issues[0].code == "figure_orphan"
    assert issues[0].placeholder_id == "rogue"


def test_cross_check_no_outline_returns_empty() -> None:
    issues = cross_check_figure_ids(None, ["any"])
    assert issues == []


def test_load_outline_from_disk(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "DRAFT_OUTLINE.md").write_text(_GOOD_OUTLINE, encoding="utf-8")
    o = load_outline(tmp_path)
    assert o is not None
    assert len(o.figures) == 3


def test_load_outline_absent_returns_none(tmp_path: Path) -> None:
    assert load_outline(tmp_path) is None


def test_paper_structural_minimums_flags_figure_orphan(tmp_path: Path) -> None:
    """Integration: structural minimums emits draft_outline_figure_orphan
    when main.tex contains a fig label not present in DRAFT_OUTLINE.md."""
    from argus_skill.skills.paper_structural_minimums import (
        validate_paper_structural_minimums,
    )
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "DRAFT_OUTLINE.md").write_text(_GOOD_OUTLINE, encoding="utf-8")
    figs_dir = paper / "figures"
    figs_dir.mkdir()
    (figs_dir / "F1_teaser.pdf").write_text("placeholder", encoding="utf-8")
    (paper / "main.tex").write_text(
        r"""
\documentclass{article}
\begin{document}
\section{Intro}
\begin{figure}\includegraphics{figures/F1_teaser.pdf}\label{fig:F1_teaser}\end{figure}
\begin{figure}\includegraphics{figures/F1_teaser.pdf}\label{fig:rogue_added_adhoc}\end{figure}
\end{document}
""",
        encoding="utf-8",
    )
    report = validate_paper_structural_minimums(tmp_path)
    codes = [i.code for i in report.issues]
    assert "draft_outline_figure_orphan" in codes
    orphan = next(i for i in report.issues
                  if i.code == "draft_outline_figure_orphan")
    assert "rogue_added_adhoc" in orphan.detail
