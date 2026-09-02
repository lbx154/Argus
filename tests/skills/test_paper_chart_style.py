"""Shared publication chart style helper (paper_chart_style.py).

The helper is the single source of truth for how DATA figures look in a paper:
colour-blind-safe palettes, venue-aware figure sizes, font embedding, and an
"emphasise Ours" convention. matplotlib is an optional (project-venv) dependency,
so the matplotlib-touching tests skip cleanly when it is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.verticals.research.skills.engineer.figure_spec_scripts import (
    paper_chart_style as pcs,
)

# ---- pure helpers (no matplotlib) -----------------------------------------

def test_palettes_are_named_and_colorblind_set() -> None:
    names = pcs.available_palettes()
    assert set(names) == {"colorblind", "muted", "high_contrast"}
    for name in names:
        colors = pcs.PALETTES[name]
        assert len(colors) >= 6
        assert all(c.startswith("#") and len(c) == 7 for c in colors)


def test_figure_size_two_column_vs_single_column_venue() -> None:
    # Two-column venue: single vs double are different physical widths.
    single = pcs.figure_size("single", venue="EMNLP")
    double = pcs.figure_size("double", venue="EMNLP")
    assert single[0] < double[0]
    assert double[0] > 6.0  # full text width
    assert single[0] < 3.6  # one column
    # Single-column venue: one text width regardless of the column arg.
    ncol = pcs.figure_size("single", venue="NeurIPS")
    assert ncol[0] > single[0]
    assert pcs.figure_size("double", venue="NeurIPS")[0] == ncol[0]


def test_is_two_column_inference() -> None:
    assert pcs._is_two_column("EMNLP") is True
    assert pcs._is_two_column("AAAI") is True
    assert pcs._is_two_column(None) is True  # default: two-column
    assert pcs._is_two_column("NeurIPS") is False
    assert pcs._is_two_column("icml") is False


# ---- matplotlib-backed behaviour ------------------------------------------

def test_set_pub_style_applies_and_returns_palette() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib as mpl

    colors = pcs.set_pub_style(venue="EMNLP", column="double", palette="muted")
    assert colors == pcs.PALETTES["muted"]
    # Font embedding for camera-ready PDFs.
    assert mpl.rcParams["pdf.fonttype"] == 42
    # Figure size reflects the requested (venue, column).
    assert tuple(mpl.rcParams["figure.figsize"]) == pcs.figure_size("double", venue="EMNLP")
    # Palette is installed on the prop cycle.
    cycle_colors = mpl.rcParams["axes.prop_cycle"].by_key()["color"]
    assert cycle_colors[0] == pcs.PALETTES["muted"][0]


def test_set_pub_style_unknown_palette_falls_back_to_default() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    colors = pcs.set_pub_style(palette="does-not-exist")
    assert colors == pcs.PALETTES[pcs.DEFAULT_PALETTE]


def test_set_pub_style_requires_scienceplots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("matplotlib")

    real_import = pcs.importlib.import_module

    def reject_scienceplots(name: str, package: str | None = None):
        if name == "scienceplots":
            raise ModuleNotFoundError("No module named 'scienceplots'")
        return real_import(name, package)

    monkeypatch.setattr(pcs.importlib, "import_module", reject_scienceplots)

    with pytest.raises(RuntimeError, match=r"argus-skill\[figures\]"):
        pcs.set_pub_style()


def test_research_data_figures_have_one_renderer_path() -> None:
    skill_root = pcs.__file__.rsplit("figure_spec_scripts", 1)[0]

    chart_skill = Path(skill_root, "paper-chart-styling.md").read_text(encoding="utf-8")
    analysis_skill = Path(
        skill_root, "research-results-analysis-and-figures.md"
    ).read_text(encoding="utf-8")
    router_skill = Path(
        skill_root, "research-visualization-router.md"
    ).read_text(encoding="utf-8")

    normalized_chart = " ".join(chart_skill.split())
    normalized_analysis = " ".join(analysis_skill.split())
    normalized_router = " ".join(router_skill.split())

    assert "SciencePlots is mandatory for this route" in normalized_chart
    assert "single SciencePlots/Matplotlib data-figure path" in normalized_analysis
    assert "Any paper data/metric/result chart" in normalized_router
    assert "keep the LiveFigure-style conceptual-figure route" in normalized_router


def test_highlight_ours_bars_emphasises_ours_and_greys_baselines() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pcs.set_pub_style(venue="EMNLP")
    fig, ax = plt.subplots()
    ax.bar(["a", "b", "c"], [1.0, 2.0, 3.0])
    pcs.highlight_ours(ax, ours_index=2)
    bars = ax.patches
    # Ours (index 2) keeps a dark outline + full alpha; baselines fade to grey.
    assert bars[2].get_edgecolor()[:3] == (0.0, 0.0, 0.0)
    assert bars[2].get_alpha() in (None, 1.0)
    assert bars[0].get_alpha() == pytest.approx(0.85)
    plt.close(fig)


def test_highlight_ours_grouped_bars_emphasises_the_whole_series() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pcs.set_pub_style(venue="EMNLP")
    fig, ax = plt.subplots()
    x = [0, 1]
    ax.bar(
        [value - 0.2 for value in x],
        [1.0, 1.5],
        width=0.2,
        yerr=[0.1, 0.1],
        capsize=2,
    )
    ax.bar(x, [1.4, 1.8], width=0.2, yerr=[0.1, 0.1], capsize=2)
    ax.bar(
        [value + 0.2 for value in x],
        [1.7, 2.1],
        width=0.2,
        yerr=[0.1, 0.1],
        capsize=2,
    )
    pcs.highlight_ours(ax, ours_index=2)

    bar_containers = [
        container for container in ax.containers if hasattr(container, "patches")
    ]
    assert len(bar_containers) == 3
    for index, container in enumerate(bar_containers):
        for bar in container.patches:
            if index == 2:
                assert bar.get_edgecolor()[:3] == (0.0, 0.0, 0.0)
                assert bar.get_alpha() in (None, 1.0)
            else:
                assert bar.get_alpha() == pytest.approx(0.85)
    plt.close(fig)


def test_highlight_ours_lines_thickens_ours() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pcs.set_pub_style(venue="EMNLP")
    fig, ax = plt.subplots()
    for _ in range(3):
        ax.plot([0, 1, 2], [0, 1, 2])
    pcs.highlight_ours(ax, ours_index=0)
    lines = ax.get_lines()
    assert lines[0].get_linewidth() > lines[1].get_linewidth()
    assert lines[0].get_zorder() > lines[1].get_zorder()
    plt.close(fig)


def test_demo_renders_before_and_after(tmp_path) -> None:
    pytest.importorskip("matplotlib")
    written = pcs._demo(str(tmp_path))
    assert len(written) == 2
    for path in written:
        from pathlib import Path

        assert Path(path).is_file() and Path(path).stat().st_size > 0
