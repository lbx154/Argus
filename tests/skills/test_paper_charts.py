"""The data-figure helper draws the chart, so the look is the paper's, not the script's.

Past control projects drew bars from a truncated axis, pinned framed legends
on the data, typed colours by hand and averaged seeds into bars with no
spread. ``paper_charts`` takes data and names and makes those choices itself,
records what it encoded, and exports PDF + PNG the manuscript can use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
pytest.importorskip("scienceplots")
matplotlib.use("Agg")

from argus.verticals.research.skills.engineer.figure_spec_scripts import (  # noqa: E402
    paper_charts as pc,
)

REPEATS = {
    "Baseline": [[91.0, 89.0, 90.0], [70.0, 72.0, 71.0]],
    "Ours": [[95.0, 96.0, 95.5], [93.0, 92.0, 93.5]],
}


def _paper(root: Path) -> None:
    (root / "paper").mkdir(parents=True, exist_ok=True)
    (root / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")


def test_bars_start_at_zero_show_repeats_and_put_the_legend_outside(tmp_path: Path) -> None:
    fig, ax = pc.bars(["4k", "32k"], REPEATS, ours="Ours", ylabel="acc (%)", two_column=True)

    assert ax.get_ylim()[0] == 0
    assert ax.get_legend() is None and len(fig.legends) == 1
    labels = [t.get_text() for t in fig.legends[0].get_texts()]
    assert labels == ["Baseline", "Ours"]
    # the proposed method is drawn last (on top) with a black edge; error bars exist
    ours_bars = [c for c in ax.containers if c.get_label() == "Ours"][0]
    assert ours_bars.patches[0].get_edgecolor()[:3] == (0.0, 0.0, 0.0)
    assert ours_bars.errorbar is not None

    _paper(tmp_path)
    written = pc.save(fig, tmp_path / "paper" / "figures" / "acc", inputs=["results/x.json"], project_root=tmp_path)
    assert Path(written["pdf"]).is_file() and Path(written["png"]).is_file()
    facts = json.loads(Path(written["facts"]).read_text(encoding="utf-8"))
    panel = facts["panels"][0]
    assert panel["kind"] == "bars" and panel["axis_from_zero"] is True and panel["error_bars"] is True
    assert panel["legend"] == "above"
    assert [s["repeats"] for s in panel["series"]] == [3, 3]
    assert facts["inputs"] == ["results/x.json"]


def test_a_truncated_bar_axis_needs_a_reason_and_records_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="truncated_reason"):
        pc.bars(["a"], {"x": [90.0], "y": [91.0]}, ylim=(80, 95), two_column=True)

    fig, ax = pc.bars(
        ["a"], {"x": [90.0], "y": [91.0]}, ylim=(80, 95), truncated_reason="all methods above 80", two_column=True
    )
    _paper(tmp_path)
    written = pc.save(fig, tmp_path / "paper" / "figures" / "cut", project_root=tmp_path)
    panel = json.loads(Path(written["facts"]).read_text(encoding="utf-8"))["panels"][0]
    assert panel["axis_from_zero"] is False and panel["truncated_reason"] == "all methods above 80"


def test_lines_get_a_log2_axis_for_powers_of_two_and_bands_for_repeats() -> None:
    x = [1024, 2048, 4096, 8192]
    data = {"Base": [[1, 1.2], [2, 2.1], [4, 4.2], [8, 8.1]], "Ours": [1.0, 1.9, 3.8, 7.5]}
    fig, ax = pc.lines(x, data, ours="Ours", two_column=True)

    assert ax.get_xscale() == "log"
    assert len(ax.collections) == 1  # one band for the repeated series
    ours_line = [line for line in ax.get_lines() if line.get_label() == "Ours"][0]
    base_line = [line for line in ax.get_lines() if line.get_label() == "Base"][0]
    assert ours_line.get_linewidth() > base_line.get_linewidth()
    assert ours_line.get_linestyle() == "-" and base_line.get_linestyle() != "-"
    matplotlib.pyplot.close(fig)


def test_a_log_y_axis_refuses_a_zero_instead_of_a_sentinel() -> None:
    with pytest.raises(ValueError, match="symlog"):
        pc.lines([1, 2, 3], {"a": [0.0, 1.0, 2.0]}, yscale="log", two_column=True)


def test_dots_label_every_point_and_name_the_proposed_method_in_bold() -> None:
    fig, ax = pc.dots(
        {"A": (16, 96.4), "B": (4, 90.4, 0.4), "Ours": (2.25, 90.9, 0.5)},
        ours="Ours", better="upper left", two_column=True,
    )
    texts = [t.get_text() for t in ax.texts]
    assert {"A", "B", "Ours"} <= set(texts)
    ours = [t for t in ax.texts if t.get_text() == "Ours"][0]
    assert ours.get_fontweight() == "bold"
    assert any(t.get_text().startswith("better") for t in ax.texts)
    matplotlib.pyplot.close(fig)


def test_panels_share_one_legend_and_carry_letters(tmp_path: Path) -> None:
    fig, axes = pc.grid(1, 2, column="double", two_column=True)
    pc.bars(["4k", "32k"], REPEATS, ours="Ours", ax=axes[0])
    pc.lines([1, 2], {"Baseline": [1, 2], "Ours": [1, 3]}, ours="Ours", ax=axes[1])
    pc.finish(fig)

    assert len(fig.legends) == 1
    assert [t.get_text() for t in fig.legends[0].get_texts()] == ["Baseline", "Ours"]
    assert all(ax.get_legend() is None for ax in axes)
    letters = [t.get_text() for ax in axes for t in ax.texts]
    assert letters == ["(a)", "(b)"]
    _paper(tmp_path)
    written = pc.save(fig, tmp_path / "paper" / "figures" / "panels", project_root=tmp_path)
    facts = json.loads(Path(written["facts"]).read_text(encoding="utf-8"))
    assert [p["kind"] for p in facts["panels"]] == ["bars", "lines"]
    assert all(p["legend"] == "above" for p in facts["panels"])


def test_exports_carry_truetype_fonts_and_name_the_helper(tmp_path: Path) -> None:
    from argus.verticals.research import figure_lint

    fig, _ = pc.bars(["a", "b"], {"x": [1.0, 2.0], "Ours": [2.0, 3.0]}, ours="Ours", two_column=True)
    _paper(tmp_path)
    written = pc.save(fig, tmp_path / "paper" / "figures" / "fonts", project_root=tmp_path)
    _, subtypes = figure_lint._pdf_producer_and_fonts(Path(written["pdf"]))
    assert "/Type3" not in subtypes
    chunks = figure_lint._png_text_chunks(Path(written["png"]))
    assert chunks["Software"].startswith("paper_charts")
    assert json.loads(chunks["paper_charts:facts"])["figure"] == "fonts"
