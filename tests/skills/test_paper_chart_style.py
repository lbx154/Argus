"""Shared publication chart style helper (paper_chart_style.py).

The helper is the single source of truth for how DATA figures look in a paper:
colour-blind-safe palettes, venue-aware figure sizes (column layout read from
the project's researched venue profile), font embedding, and an "emphasise
Ours" convention. matplotlib is an optional (project-venv) dependency, so the
matplotlib-touching tests skip cleanly when it is absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.verticals.research.skills.engineer.figure_spec_scripts import (
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


def test_column_layout_comes_from_researched_venue_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The researched research/VENUE_PROFILE.json decides the layout; walking up
    # from a nested working directory finds it.
    research = tmp_path / "research"
    research.mkdir()
    (research / "VENUE_PROFILE.json").write_text(
        json.dumps({"key": "JOURNALX", "two_column": False}), encoding="utf-8"
    )
    nested = tmp_path / "paper" / "analysis"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert pcs._project_two_column() is False
    assert pcs.figure_size("double")[0] == pcs.figure_size("single")[0]

    (research / "VENUE_PROFILE.json").write_text(
        json.dumps({"key": "CONFY", "two_column": True}), encoding="utf-8"
    )
    assert pcs._project_two_column() is True
    assert pcs.figure_size("double")[0] > pcs.figure_size("single")[0]


@pytest.mark.parametrize("profile", [None, "not json", "{}", '{"two_column":"false"}'])
def test_column_layout_requires_template_choice_without_valid_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    if profile is not None:
        research = tmp_path / "research"
        research.mkdir()
        (research / "VENUE_PROFILE.json").write_text(profile, encoding="utf-8")
    with pytest.raises(ValueError, match="Read its current template"):
        pcs.figure_size()
    # An explicit choice works without a cached profile, including for ICLR's
    # single-column template. Unknown metadata must not silently narrow it.
    assert pcs.figure_size("single", two_column=False)[0] == 5.5
    assert pcs.figure_size("single", two_column=True)[0] == 3.3


# ---- matplotlib-backed behaviour ------------------------------------------


def test_same_heatmap_value_needs_different_text_after_colormap_reversal() -> None:
    colors = pytest.importorskip("matplotlib.colors")
    cmap = colors.ListedColormap(["#CBE9D3", "#243D4E"])
    norm = colors.Normalize(vmin=0, vmax=10)
    assert pcs.contrast_text_color(cmap(norm(3))) == "black"
    assert pcs.contrast_text_color(cmap.reversed()(norm(3))) == "white"


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

    with pytest.raises(RuntimeError, match=r"argus\[figures\]"):
        pcs.set_pub_style()


def test_research_data_figures_have_one_renderer_path() -> None:
    skill_root = pcs.__file__.rsplit("figure_spec_scripts", 1)[0]

    chart_skill = Path(skill_root, "paper-chart-styling.md").read_text(encoding="utf-8")
    # The figure studio now carries the routing the visualization router and
    # the results-analysis skill used to state separately.
    studio_skill = Path(
        skill_root, "paper-framework-figure-studio.md"
    ).read_text(encoding="utf-8")

    normalized_chart = " ".join(chart_skill.split())
    normalized_studio = " ".join(studio_skill.split())

    assert "The route depends on SciencePlots" in normalized_chart
    assert "single SciencePlots/Matplotlib data-figure path" in normalized_studio
    assert "Any paper data/metric/result chart" in normalized_studio
    assert "Composing a conceptual paper figure" in normalized_studio


def test_chart_skill_has_no_builtin_venue_api() -> None:
    # The skill's examples must match the real API: no venue= parameter, no
    # built-in venue keys — layout comes from the researched profile.
    skill_root = pcs.__file__.rsplit("figure_spec_scripts", 1)[0]
    chart_skill = Path(skill_root, "paper-chart-styling.md").read_text(encoding="utf-8")
    assert 'venue="' not in chart_skill
    assert "venue=" not in chart_skill
    for literal in ("EMNLP", "AAAI", "NeurIPS"):
        assert literal not in chart_skill


def test_highlight_ours_bars_emphasises_ours_and_greys_baselines() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pcs.set_pub_style(two_column=True)
    fig, ax = plt.subplots()
    ax.bar(["a", "b", "c"], [1.0, 2.0, 3.0])
    pcs.highlight_ours(ax, ours_index=2)
    bars = ax.patches
    # Ours (index 2) keeps a dark outline + full alpha; baselines fade to grey.
    assert bars[2].get_edgecolor()[:3] == (0.0, 0.0, 0.0)
    assert bars[2].get_alpha() in (None, 1.0)
    assert bars[0].get_alpha() == pytest.approx(0.85)
    plt.close(fig)


def test_demo_renders_before_and_after(tmp_path) -> None:
    pytest.importorskip("matplotlib")
    written = pcs._demo(str(tmp_path))
    assert len(written) == 2
    for path in written:
        assert Path(path).is_file() and Path(path).stat().st_size > 0
