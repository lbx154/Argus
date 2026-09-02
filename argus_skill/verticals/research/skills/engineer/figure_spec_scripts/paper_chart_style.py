#!/usr/bin/env python3
"""
Publication chart style — one shared matplotlib/seaborn theme for every DATA
figure in a paper (accuracy-vs-budget curves, ablation bars, latency plots, …).

Why this exists
---------------
Without a shared theme, each analysis script styles its plots differently:
default matplotlib blue/orange, no font embedding, rainbow/``jet`` colormaps,
tick labels in the wrong size, colours that collapse to identical greys under
colour-blind simulation. The result reads as "ugly and inconsistent" next to a
real conference paper. This module gives every figure ONE journal-grade look:

* Required SciencePlots ``['science','no-latex']`` base (thin spines, inward
  ticks, serif-ish math). Data-figure generation fails with an actionable
  installation error rather than silently reverting to a different renderer.
* Three named, **colour-blind-safe** palettes (seaborn) so figures are
  distinguishable in print and under CVD: ``colorblind`` (default),
  ``muted`` (cool journal tone), ``high_contrast`` (talks/posters).
* Font embedding for camera-ready PDFs (``pdf.fonttype = 42`` / TrueType) and
  ``savefig`` at 600 dpi with a tight bbox.
* Venue-aware figure sizes: a single-column float (``figure``) vs a full-width
  float (``figure*``) get the right physical width so nothing is up/down-scaled
  in LaTeX (which is what makes fonts look wrong).
* A ``highlight_ours`` convention: baselines are de-emphasised, OUR method is
  saturated + outlined, so the reader's eye lands on the right series.

This file is intentionally dependency-light and self-contained: the
``paper-chart-styling`` skill copies it into ``paper/analysis/`` and analysis
scripts do ``from paper_chart_style import set_pub_style`` — the project venv
does not need ``argus_skill`` on its path.

Usage
-----
    from paper_chart_style import set_pub_style, highlight_ours, figure_size

    colors = set_pub_style(venue="EMNLP", column="double", palette="colorblind")
    fig, ax = plt.subplots(figsize=figure_size(column="double"))
    ...
    highlight_ours(ax, ours_index=2)   # emphasise the "Ours" series
    fig.savefig("paper/figures/main_results.pdf")

Run ``python3 paper_chart_style.py`` to render a before/after demo comparison.
"""
from __future__ import annotations

import importlib
import sys
from typing import Sequence

# ---------------------------------------------------------------------------
# Palettes — every one is colour-blind-safe (checked against deuteranopia /
# protanopia). Keys are stable; the skill and the analysis scripts refer to
# them by name.
# ---------------------------------------------------------------------------
PALETTES: dict[str, list[str]] = {
    # seaborn's Wong-based colour-blind palette — the safe default.
    "colorblind": [
        "#0173B2", "#DE8F05", "#029E73", "#D55E00",
        "#CC78BC", "#CA9161", "#FBAFE4", "#949494",
    ],
    # cooler, lower-saturation journal tone (seaborn "muted"-like).
    "muted": [
        "#4878D0", "#EE854A", "#6ACC64", "#D65F5F",
        "#956CB4", "#8C613C", "#DC7EC0", "#797979",
    ],
    # high-contrast set for talks / posters / projector legibility.
    "high_contrast": [
        "#004488", "#DDAA33", "#BB5566", "#000000",
        "#33BBEE", "#EE7733", "#009988", "#CC3311",
    ],
}
DEFAULT_PALETTE = "colorblind"

# Neutral grey used to de-emphasise baselines when highlighting "Ours".
BASELINE_GREY = "#9A9A9A"

# Venue families that are SINGLE-column (NeurIPS/ICML/…): a full-page-width
# text block, no ``figure*`` distinction. Everything else is treated as a
# two-column venue (EMNLP/ACL/AAAI/CVPR-style).
_SINGLE_COLUMN_VENUES = {
    "NEURIPS", "NIPS", "ICML", "ICLR", "JMLR", "TMLR", "COLM", "RLC",
}


def _is_two_column(venue: str | None) -> bool:
    if not venue:
        return True
    return venue.strip().upper() not in _SINGLE_COLUMN_VENUES


def figure_size(
    column: str = "single",
    *,
    venue: str | None = None,
    aspect: float = 0.66,
) -> tuple[float, float]:
    """Physical figure size (inches) matching the LaTeX float it will sit in.

    ``column='single'`` → a one-column ``figure``; ``column='double'`` → a
    full-width ``figure*``. Widths follow the real text/column widths so LaTeX
    does not rescale the graphic (rescaling is what warps the fonts).
    """
    two_col = _is_two_column(venue)
    if two_col:
        width = 6.9 if column == "double" else 3.3   # \textwidth vs \columnwidth
    else:
        width = 5.5                                   # single-column \textwidth
    return (width, round(width * aspect, 2))


def _apply_scienceplots_style() -> None:
    """Register and apply the required publication style."""
    import matplotlib.pyplot as plt

    try:
        importlib.import_module("scienceplots")
    except ImportError as exc:
        raise RuntimeError(
            "SciencePlots is required for research data figures. Install the "
            "plotting stack with `pip install 'argus-skill[figures]'` or "
            "`pip install matplotlib seaborn SciencePlots`."
        ) from exc
    plt.style.use(["science", "no-latex"])


def set_pub_style(
    venue: str | None = None,
    *,
    column: str = "single",
    palette: str = DEFAULT_PALETTE,
) -> list[str]:
    """Apply the shared publication style and return the active colour list.

    Safe to call once at the top of an analysis script. SciencePlots is a
    required dependency for this data-figure path; a missing installation is
    surfaced instead of producing a visually inconsistent fallback. Returns
    the palette so callers can cycle colours explicitly (``colors[i]``) when
    auto-cycling is not enough.
    """
    import matplotlib as mpl
    colors = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])

    _apply_scienceplots_style()

    # Sizes tuned for 8–9pt body text at final print size.
    two_col = _is_two_column(venue)
    base = 9 if two_col else 10
    mpl.rcParams.update(
        {
            "figure.figsize": figure_size(column, venue=venue),
            "figure.dpi": 150,
            "savefig.dpi": 600,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            # Embed fonts as TrueType so camera-ready PDFs pass font checks.
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.size": base,
            "axes.titlesize": base + 1,
            "axes.labelsize": base,
            "xtick.labelsize": base - 1,
            "ytick.labelsize": base - 1,
            "legend.fontsize": base - 1,
            "lines.linewidth": 1.6,
            "lines.markersize": 5,
            "axes.prop_cycle": mpl.cycler(color=colors),
            "axes.grid": True,
            "grid.alpha": 0.3,
            "legend.frameon": False,
        }
    )
    return list(colors)


def highlight_ours(
    ax,
    ours_index: int,
    *,
    baseline_grey: str = BASELINE_GREY,
    ours_color: str | None = None,
) -> None:
    """De-emphasise baselines and make the OUR series pop.

    Works for both bar charts and line plots. With multiple bar containers,
    ``ours_index`` selects one plotted series and styles every bar in that
    container. With one bar container, it selects one category for backward
    compatibility. For line plots it selects one line. Baselines fade to
    neutral grey; Ours keeps full saturation, gains a dark outline / heavier
    weight, and is drawn on top.
    """
    # --- bar charts -------------------------------------------------------
    containers = [
        container
        for container in getattr(ax, "containers", [])
        if getattr(container, "patches", None)
    ]
    if containers:
        grouped = len(containers) > 1
        for container_index, container in enumerate(containers):
            for category_index, bar in enumerate(container.patches):
                selected = (
                    container_index == ours_index
                    if grouped
                    else category_index == ours_index
                )
                if selected:
                    if ours_color:
                        bar.set_facecolor(ours_color)
                    bar.set_edgecolor("black")
                    bar.set_linewidth(1.2)
                    bar.set_alpha(1.0)
                    bar.set_zorder(3)
                else:
                    bar.set_facecolor(baseline_grey)
                    bar.set_alpha(0.85)
                    bar.set_zorder(2)
        return

    # --- line charts ------------------------------------------------------
    lines = ax.get_lines()
    for i, line in enumerate(lines):
        if i == ours_index:
            if ours_color:
                line.set_color(ours_color)
            line.set_linewidth(2.6)
            line.set_alpha(1.0)
            line.set_zorder(5)
            line.set_markersize(7)
            line.set_markeredgecolor("black")
            line.set_markeredgewidth(0.6)
        else:
            line.set_color(baseline_grey)
            line.set_linewidth(1.3)
            line.set_alpha(0.9)
            line.set_zorder(2)


def available_palettes() -> Sequence[str]:
    """Names of the built-in colour-blind-safe palettes."""
    return tuple(PALETTES.keys())


# ---------------------------------------------------------------------------
# Demo: render a before/after comparison so the style can be eyeballed.
#   python3 paper_chart_style.py [output_dir]
# ---------------------------------------------------------------------------
def _demo(out_dir: str = "/tmp") -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [1, 2, 4, 8, 16]
    series = {
        "Greedy": [61.2, 63.1, 64.0, 64.3, 64.4],
        "Beam": [62.0, 64.5, 66.1, 66.8, 67.0],
        "Ours": [63.5, 67.2, 70.4, 72.1, 72.9],
    }
    written: list[str] = []

    # BEFORE — default matplotlib, no shared style.
    plt.rcParams.update(plt.rcParamsDefault)
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    for name, ys in series.items():
        ax.plot(x, ys, marker="o", label=name)
    ax.set_xlabel("compute budget")
    ax.set_ylabel("accuracy")
    ax.set_xscale("log", base=2)
    ax.legend()
    ax.set_title("before")
    before = f"{out_dir}/chart_before.png"
    fig.savefig(before, dpi=200, bbox_inches="tight")
    plt.close(fig)
    written.append(before)

    # AFTER — shared publication style + highlight Ours.
    set_pub_style(venue="EMNLP", column="single", palette="colorblind")
    fig, ax = plt.subplots()
    markers = ["o", "s", "D"]
    for (name, ys), m in zip(series.items(), markers):
        ax.plot(x, ys, marker=m, label=name)
    ax.set_xlabel("compute budget (log$_2$)")
    ax.set_ylabel("accuracy (%)")
    ax.set_xscale("log", base=2)
    highlight_ours(ax, ours_index=2)
    ax.legend(loc="lower right")
    after = f"{out_dir}/chart_after.png"
    fig.savefig(after, dpi=200)
    plt.close(fig)
    written.append(after)
    return written


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
    for path in _demo(out):
        print(path)
