#!/usr/bin/env python3
"""Data figures the helper draws: bars, lines and dots that look like a paper's.

Why this exists
---------------
``paper_chart_style`` sets a theme and leaves the drawing to the analysis
script, and the drawings came back with the same faults every time: colours
typed by hand, a framed legend sitting on the data, bars that start at 30 to
make a small gap look large, five seeds averaged into one bar with no spread,
a title repeating the caption. The look of a figure is decided by those calls,
not by the theme, so this module makes the calls. An analysis script passes
data and names; the helper decides colours, emphasis, legend placement, axis
origin, error bars and export, the same way for every figure in the paper.

Usage
-----
    from paper_charts import bars, lines, dots, grid, finish, save

    fig, ax = bars(
        ["4k", "8k", "16k", "32k"],
        {"KIVI": [[91, 89, 90], [84, 86, 85], [70, 72, 71], [35, 38, 36]],
         "Ours": [[95, 96, 95], [94, 95, 94], [93, 92, 93], [90, 91, 90]]},
        ours="Ours", xlabel="Context length", ylabel="RULER accuracy (%)")
    save(fig, "paper/figures/ruler_accuracy", inputs=["results/ruler.json"])

A series is a list of numbers, or a list of lists when runs were repeated; the
helper draws the mean with the spread (std over repeats) as error bars or
bands and records the repeat count. Bars start at zero; a truncated bar axis
needs ``truncated_reason=`` and the reason is recorded for the caption. One
legend sits outside the panels. ``save`` writes ``<stem>.pdf`` (TrueType
fonts) and ``<stem>.png`` at manuscript width for inspection, plus
``paper/figures/src/<stem>/facts.json`` with what the figure encodes.

The file is self-contained beside ``paper_chart_style.py``: the skill copies
both into ``paper/analysis/`` so the project venv needs only the plotting
stack, not ``argus``.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # imported as part of the figure_spec_scripts package
    from . import paper_chart_style as _style
except ImportError:  # copied beside the analysis scripts
    import paper_chart_style as _style  # type: ignore[no-redef]

HELPER = "paper_charts"
SOFTWARE = f"{HELPER}/matplotlib"
RENDERER = "paper_charts"
FACTS_DIR = Path("paper/figures/src")

# The proposed method takes the palette's accent; baselines cycle through the rest.
OURS_COLOR: dict[str, str] = {
    "colorblind": "#D55E00",
    "muted": "#D65F5F",
    "high_contrast": "#BB5566",
}
MARKERS = ("o", "s", "D", "^", "v", "P", "X", "<", ">")
BASELINE_DASHES = ("--", "-.", ":", (0, (5, 1, 1, 1)), (0, (3, 1)))
SANS_FONTS = ["Helvetica", "Arial", "Inter", "Liberation Sans", "Nimbus Sans", "DejaVu Sans"]
LEGEND_MAX_COLUMNS = 4
LABEL_ROTATION_CHARS = 36
PNG_DPI = 200
HEADROOM = 1.12


@dataclass
class Series:
    name: str
    mean: list[float]
    spread: list[float] | None
    repeats: int
    ours: bool = False


@dataclass
class Facts:
    kind: str
    series: list[dict[str, Any]] = field(default_factory=list)
    axis_from_zero: bool | None = None
    truncated_reason: str = ""
    legend: str = ""
    error_bars: bool = False
    missing_points: int = 0
    xscale: str = "linear"
    yscale: str = "linear"
    column: str = "single"

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", None)}


_FACTS: dict[int, list[Facts]] = {}


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mean_spread(cell: Any) -> tuple[float, float | None, int]:
    """One point: a number, a list of repeats, or None/NaN for a missing value."""
    if cell is None:
        return math.nan, None, 0
    if _is_number(cell):
        return (float(cell), None, 1)
    rows = [float(v) for v in cell if _is_number(v) and not math.isnan(float(v))]
    if not rows:
        return math.nan, None, 0
    mean = sum(rows) / len(rows)
    if len(rows) == 1:
        return mean, None, 1
    var = sum((v - mean) ** 2 for v in rows) / (len(rows) - 1)
    return mean, math.sqrt(var), len(rows)


def _series(
    data: Mapping[str, Sequence[Any]],
    ours: str | None,
    errors: Mapping[str, Sequence[float]] | None,
) -> list[Series]:
    out: list[Series] = []
    for name, raw in data.items():
        points = [_mean_spread(cell) for cell in raw]
        mean = [p[0] for p in points]
        repeats = max((p[2] for p in points), default=0)
        spread: list[float] | None = None
        if errors and name in errors:
            spread = [float(v) for v in errors[name]]
        elif any(p[1] is not None for p in points):
            spread = [p[1] if p[1] is not None else 0.0 for p in points]
        out.append(Series(name=name, mean=mean, spread=spread, repeats=repeats, ours=(name == ours)))
    if ours is not None and not any(s.ours for s in out):
        raise ValueError(f"ours={ours!r} names no series; series are {list(data)}")
    # the proposed method is drawn last so it sits on top
    return [s for s in out if not s.ours] + [s for s in out if s.ours]


def _colors(series: list[Series], palette: str) -> dict[str, str]:
    base = list(_style.PALETTES.get(palette, _style.PALETTES[_style.DEFAULT_PALETTE]))
    accent = OURS_COLOR.get(palette, base[3] if len(base) > 3 else base[0])
    pool = [c for c in base if c.lower() != accent.lower()]
    out: dict[str, str] = {}
    index = 0
    for s in series:
        if s.ours:
            out[s.name] = accent
        else:
            out[s.name] = pool[index % len(pool)]
            index += 1
    return out


def _record(fig: Any, facts: Facts) -> None:
    _FACTS.setdefault(id(fig), []).append(facts)


def _series_facts(series: list[Series]) -> list[dict[str, Any]]:
    return [
        {"name": s.name, "ours": s.ours, "repeats": s.repeats, "spread": s.spread is not None}
        for s in series
    ]


# ---------------------------------------------------------------------------
# style and layout
# ---------------------------------------------------------------------------
def apply_style(*, column: str = "single", palette: str = _style.DEFAULT_PALETTE, two_column: bool | None = None) -> list[str]:
    """The shared theme plus the choices this helper makes on top of it."""
    import matplotlib as mpl

    colors = _style.set_pub_style(column=column, palette=palette, two_column=two_column)
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": SANS_FONTS,
            "mathtext.fontset": "dejavusans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "axes.axisbelow": True,
            "axes.linewidth": 0.7,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.top": False,
            "ytick.right": False,
            "xtick.minor.top": False,
            "ytick.minor.right": False,
            "xtick.major.size": 2.5,
            "ytick.major.size": 2.5,
            "xtick.minor.visible": False,
            "ytick.minor.visible": False,
            "legend.handlelength": 1.6,
            "legend.columnspacing": 1.2,
            "legend.borderaxespad": 0.2,
            "figure.constrained_layout.use": True,
        }
    )
    return colors


def grid(
    rows: int = 1,
    cols: int = 1,
    *,
    column: str = "double",
    aspect: float | None = None,
    palette: str = _style.DEFAULT_PALETTE,
    two_column: bool | None = None,
    sharey: bool = False,
):
    """A figure of panels sized for its float; draw into each with ``ax=``, then ``finish``."""
    import matplotlib.pyplot as plt

    apply_style(column=column, palette=palette, two_column=two_column)
    width, _ = _style.figure_size(column, two_column=two_column)
    panel = width / cols
    ratio = aspect if aspect is not None else (0.72 if cols > 1 else 0.62)
    fig, axes = plt.subplots(rows, cols, figsize=(width, panel * ratio * rows), sharey=sharey, layout="constrained")
    fig.set_facecolor("white")
    return fig, axes


def _single(column: str, palette: str, two_column: bool | None, aspect: float | None):
    import matplotlib.pyplot as plt

    apply_style(column=column, palette=palette, two_column=two_column)
    width, height = _style.figure_size(column, two_column=two_column, aspect=aspect if aspect is not None else 0.62)
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    fig.set_facecolor("white")
    return fig, ax


def _ordered_handles(ax: Any, order: Sequence[str] | None = None):
    handles, labels = ax.get_legend_handles_labels()
    if order:
        rank = {name: i for i, name in enumerate(order)}
        pairs = sorted(zip(handles, labels), key=lambda hl: rank.get(hl[1], len(rank)))
        handles, labels = [h for h, _ in pairs], [label for _, label in pairs]
    return handles, labels


def _legend(fig: Any, ax: Any, placement: str | None, n_series: int, order: Sequence[str] | None = None) -> str:
    if placement is None or n_series < 2:
        return "none"
    handles, labels = _ordered_handles(ax, order)
    if placement == "inside":
        ax.legend(handles, labels, loc="best", frameon=False)
        return "inside"
    ncol = min(len(labels), LEGEND_MAX_COLUMNS)
    fig.legend(handles, labels, loc="outside upper center", ncol=ncol, frameon=False)
    return "above"


def finish(fig: Any, *, legend: str | None = "above", letters: bool = True) -> None:
    """One legend for all panels (duplicates merged) and panel letters (a), (b), …"""
    seen: dict[str, Any] = {}
    for ax in fig.axes:
        for handle, label in zip(*ax.get_legend_handles_labels()):
            seen.setdefault(label, handle)
    if legend == "above" and len(seen) > 1:
        fig.legend(list(seen.values()), list(seen), loc="outside upper center", ncol=min(len(seen), LEGEND_MAX_COLUMNS), frameon=False)
    elif legend == "inside" and len(seen) > 1:
        fig.axes[0].legend(list(seen.values()), list(seen), loc="best", frameon=False)
    if letters and len(fig.axes) > 1:
        for index, ax in enumerate(fig.axes):
            ax.text(0.0, 1.02, f"({chr(97 + index)})", transform=ax.transAxes, ha="left", va="bottom", fontweight="bold")
    for facts in _FACTS.get(id(fig), []):
        facts.legend = legend or "none"


def _label_axes(ax: Any, xlabel: str | None, ylabel: str | None) -> None:
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)


def _rotate_if_long(ax: Any, labels: Sequence[str]) -> None:
    if sum(len(str(label)) for label in labels) > LABEL_ROTATION_CHARS:
        for tick in ax.get_xticklabels():
            tick.set_rotation(20)
            tick.set_ha("right")


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def bars(
    categories: Sequence[str],
    data: Mapping[str, Sequence[Any]],
    *,
    ours: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    errors: Mapping[str, Sequence[float]] | None = None,
    ylim: tuple[float, float] | None = None,
    truncated_reason: str = "",
    annotate: bool = False,
    fmt: str = "{:.1f}",
    reference: float | None = None,
    reference_label: str = "",
    legend: str | None = "above",
    column: str = "single",
    palette: str = _style.DEFAULT_PALETTE,
    two_column: bool | None = None,
    aspect: float | None = None,
    ax: Any = None,
):
    """Grouped bars per category, the proposed method emphasised, bars from zero.

    ``data`` maps a series name to one value per category; a value may be a
    list of repeats (drawn as mean with std error bars). ``reference`` draws a
    dashed horizontal line (an upper bound such as the full-precision model).
    A ``ylim`` whose lower bound is above zero needs ``truncated_reason``.
    """
    series = _series(data, ours, errors)
    if ylim is not None and ylim[0] > 0 and not truncated_reason.strip():
        raise ValueError(
            "bars encode value by length, so the axis starts at zero; pass truncated_reason='…' "
            "to start higher and put that reason in the caption"
        )
    fig, axis = (ax.figure, ax) if ax is not None else _single(column, palette, two_column, aspect)
    colors = _colors(series, palette)
    n = len(series)
    width = 0.8 / max(n, 1)
    facts = Facts(kind="bars", series=_series_facts(series), column=column)
    top = 0.0
    for slot, s in enumerate(series):
        offset = (slot - (n - 1) / 2) * width
        xs = [i + offset for i in range(len(categories))]
        ys = [0.0 if math.isnan(v) else v for v in s.mean]
        facts.missing_points += sum(1 for v in s.mean if math.isnan(v))
        spread = s.spread
        axis.bar(
            xs,
            ys,
            width * 0.94,
            label=s.name,
            color=colors[s.name],
            edgecolor="black" if s.ours else "white",
            linewidth=0.8 if s.ours else 0.4,
            zorder=3 if s.ours else 2,
            alpha=1.0 if s.ours else 0.92,
            yerr=spread,
            error_kw={"ecolor": "#333333", "elinewidth": 0.7, "capsize": 1.8, "capthick": 0.7, "zorder": 4},
        )
        if spread is not None:
            facts.error_bars = True
        top = max(top, max((y + (spread[i] if spread else 0.0) for i, y in enumerate(ys)), default=0.0))
        if annotate:
            for i, (x, y) in enumerate(zip(xs, ys)):
                if math.isnan(s.mean[i]):
                    axis.text(x, 0, "n/a", ha="center", va="bottom", fontsize=6, color="#666666", rotation=90)
                    continue
                lift = (spread[i] if spread else 0.0)
                axis.text(x, y + lift, fmt.format(y), ha="center", va="bottom", fontsize=6.5,
                          color="black" if s.ours else "#333333", zorder=5)
    axis.set_xticks(range(len(categories)))
    axis.set_xticklabels([str(c) for c in categories])
    axis.tick_params(axis="x", length=0)
    _rotate_if_long(axis, categories)
    if reference is not None:
        axis.axhline(reference, color="#555555", linestyle=(0, (4, 2)), linewidth=0.8, zorder=1,
                     label=reference_label or None)
        top = max(top, reference)
    if ylim is not None:
        axis.set_ylim(*ylim)
        facts.axis_from_zero = ylim[0] <= 0
        facts.truncated_reason = truncated_reason.strip()
    else:
        axis.set_ylim(0, top * (HEADROOM + (0.04 if annotate else 0.0)) if top > 0 else 1)
        facts.axis_from_zero = True
    axis.grid(axis="y", alpha=0.3, linewidth=0.5)
    axis.set_axisbelow(True)
    _label_axes(axis, xlabel, ylabel)
    if ax is None:
        order = [s.name for s in series] + ([reference_label] if reference_label else [])
        facts.legend = _legend(fig, axis, legend, n + (1 if reference_label else 0), order)
    _record(fig, facts)
    return fig, axis


def _auto_xscale(x: Sequence[float]) -> str:
    values = [float(v) for v in x if _is_number(v) and float(v) > 0]
    if len(values) >= 3 and all(abs(math.log2(v) - round(math.log2(v))) < 1e-9 for v in values):
        return "log2"
    if len(values) >= 3 and max(values) / min(values) >= 100:
        return "log"
    return "linear"


def lines(
    x: Sequence[Any],
    data: Mapping[str, Sequence[Any]],
    *,
    ours: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    errors: Mapping[str, Sequence[float]] | None = None,
    xscale: str | None = None,
    yscale: str = "linear",
    ylim: tuple[float, float] | None = None,
    reference: float | None = None,
    reference_label: str = "",
    legend: str | None = "above",
    column: str = "single",
    palette: str = _style.DEFAULT_PALETTE,
    two_column: bool | None = None,
    aspect: float | None = None,
    ax: Any = None,
):
    """Lines with distinct markers and dashes, the proposed method solid and heavy.

    Repeats become shaded bands (mean ± std). ``xscale`` is ``"linear"``,
    ``"log"`` or ``"log2"``; when omitted, powers of two (context lengths,
    budgets) get a log2 axis. A log y axis with a zero or negative value is
    an error: annotate the zero or use ``yscale="symlog"`` instead of a sentinel.
    """
    series = _series(data, ours, errors)
    fig, axis = (ax.figure, ax) if ax is not None else _single(column, palette, two_column, aspect)
    colors = _colors(series, palette)
    scale = xscale or _auto_xscale(x)
    facts = Facts(kind="lines", series=_series_facts(series), column=column, xscale=scale, yscale=yscale)
    xs = [float(v) if _is_number(v) else i for i, v in enumerate(x)]
    categorical = any(not _is_number(v) for v in x)
    baseline_index = 0
    for s in series:
        color = colors[s.name]
        marker = MARKERS[(0 if s.ours else 1 + baseline_index) % len(MARKERS)]
        dash = "-" if s.ours else BASELINE_DASHES[baseline_index % len(BASELINE_DASHES)]
        if not s.ours:
            baseline_index += 1
        pts = [(xv, yv) for xv, yv in zip(xs, s.mean) if not math.isnan(yv)]
        facts.missing_points += len(s.mean) - len(pts)
        if yscale == "log" and any(yv <= 0 for _, yv in pts):
            raise ValueError(f"series {s.name!r} has a value ≤ 0 on a log y axis; use yscale='symlog' or annotate the zero")
        axis.plot(
            [p[0] for p in pts],
            [p[1] for p in pts],
            label=s.name,
            color=color,
            marker=marker,
            linestyle=dash,
            linewidth=2.2 if s.ours else 1.3,
            markersize=5.5 if s.ours else 4.2,
            markeredgecolor="black" if s.ours else color,
            markeredgewidth=0.6 if s.ours else 0.0,
            zorder=5 if s.ours else 3,
        )
        if s.spread is not None:
            lo = [m - e for m, e in zip(s.mean, s.spread)]
            hi = [m + e for m, e in zip(s.mean, s.spread)]
            axis.fill_between(xs, lo, hi, color=color, alpha=0.18 if s.ours else 0.12, linewidth=0, zorder=1)
            facts.error_bars = True
    if categorical:
        axis.set_xticks(range(len(x)))
        axis.set_xticklabels([str(v) for v in x])
        _rotate_if_long(axis, [str(v) for v in x])
    elif scale == "log2":
        axis.set_xscale("log", base=2)
        step = 1 if len(xs) <= 7 else 2
        axis.set_xticks(xs[::step])
        axis.set_xticklabels([_short_number(v) for v in xs[::step]])
        axis.minorticks_off()
    elif scale == "log":
        axis.set_xscale("log")
    if yscale != "linear":
        axis.set_yscale(yscale)
    if reference is not None:
        axis.axhline(reference, color="#555555", linestyle=(0, (4, 2)), linewidth=0.8, zorder=1,
                     label=reference_label or None)
    if ylim is not None:
        axis.set_ylim(*ylim)
    axis.grid(True, alpha=0.25, linewidth=0.5)
    _label_axes(axis, xlabel, ylabel)
    if ax is None:
        order = [s.name for s in series] + ([reference_label] if reference_label else [])
        facts.legend = _legend(fig, axis, legend, len(series) + (1 if reference_label else 0), order)
    _record(fig, facts)
    return fig, axis


def dots(
    points: Mapping[str, Sequence[Any]],
    *,
    ours: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    xscale: str = "linear",
    yscale: str = "linear",
    labels: bool = True,
    better: str = "",
    column: str = "single",
    palette: str = _style.DEFAULT_PALETTE,
    two_column: bool | None = None,
    aspect: float | None = None,
    ax: Any = None,
):
    """A trade-off plot: one labelled dot per method, e.g. memory vs accuracy.

    ``points`` maps a method name to ``(x, y)`` or ``(x, y, yerr)``. Names are
    written beside the dots (no legend). ``better`` such as ``"upper left"``
    draws a small arrow saying which corner is better.
    """
    fig, axis = (ax.figure, ax) if ax is not None else _single(column, palette, two_column, aspect)
    names = list(points)
    series = [Series(name=n, mean=[float(points[n][1])], spread=None, repeats=1, ours=(n == ours)) for n in names]
    if ours is not None and ours not in names:
        raise ValueError(f"ours={ours!r} names no point; points are {names}")
    series = [s for s in series if not s.ours] + [s for s in series if s.ours]
    colors = _colors(series, palette)
    facts = Facts(kind="dots", series=_series_facts(series), column=column, xscale=xscale, yscale=yscale)
    for index, s in enumerate(series):
        px, py, *rest = points[s.name]
        yerr = float(rest[0]) if rest else None
        axis.errorbar(
            [float(px)], [float(py)], yerr=None if yerr is None else [yerr],
            fmt=MARKERS[0 if s.ours else 1 + index % (len(MARKERS) - 1)],
            color=colors[s.name],
            markersize=8 if s.ours else 6,
            markeredgecolor="black" if s.ours else colors[s.name],
            markeredgewidth=0.7 if s.ours else 0.0,
            ecolor="#333333", elinewidth=0.7, capsize=2,
            zorder=5 if s.ours else 3,
        )
        if yerr is not None:
            facts.error_bars = True
    if xscale != "linear":
        axis.set_xscale("log", base=2) if xscale == "log2" else axis.set_xscale(xscale)
    if yscale != "linear":
        axis.set_yscale(yscale)
    axis.margins(x=0.12, y=0.12)
    if labels:
        _place_labels(axis, [(s.name, float(points[s.name][0]), float(points[s.name][1]), s.ours) for s in series])
    if better:
        _better_arrow(axis, better)
    axis.grid(True, alpha=0.25, linewidth=0.5)
    _label_axes(axis, xlabel, ylabel)
    facts.legend = "labels"
    _record(fig, facts)
    return fig, axis


_LABEL_OFFSETS = (
    (6, 4, "left"), (6, -10, "left"), (-6, 4, "right"), (-6, -10, "right"),
    (0, 9, "center"), (0, -15, "center"), (8, -22, "left"), (-8, -22, "right"),
    (0, 16, "center"), (0, -28, "center"), (14, -3, "left"), (-14, -3, "right"),
)


def _place_labels(axis: Any, items: Sequence[tuple[str, float, float, bool]]) -> None:
    """Names beside the dots, each at the first offset that covers no dot or earlier label."""
    fig = axis.figure
    fig.canvas.draw()
    dpi = fig.dpi
    size = 7.0
    taken: list[tuple[float, float, float, float]] = []
    for _, x, y, _ in items:
        cx, cy = axis.transData.transform((x, y))
        r = 6 * dpi / 72
        taken.append((cx - r, cy - r, cx + r, cy + r))
    ax_box = axis.get_window_extent()

    def overlaps(a, b) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    # the proposed method is labelled first so it gets the best spot
    for name, x, y, ours in sorted(items, key=lambda it: not it[3]):
        cx, cy = axis.transData.transform((x, y))
        width = 0.56 * size * len(name) * dpi / 72
        height = 1.15 * size * dpi / 72
        best, best_cost = None, math.inf
        for dx, dy, ha in _LABEL_OFFSETS:
            px, py = cx + dx * dpi / 72, cy + dy * dpi / 72
            left = px if ha == "left" else px - width if ha == "right" else px - width / 2
            box = (left, py, left + width, py + height)
            inside = ax_box.x0 <= box[0] and box[2] <= ax_box.x1 and ax_box.y0 <= box[1] and box[3] <= ax_box.y1
            cost = sum(1 for t in taken if overlaps(box, t)) + (0 if inside else 3)
            if cost < best_cost:
                best, best_cost = (dx, dy, ha, box), cost
            if cost == 0:
                break
        assert best is not None
        dx, dy, ha, box = best
        taken.append(box)
        axis.annotate(name, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=size, ha=ha,
                      va="bottom", fontweight="bold" if ours else "normal", color="black" if ours else "#333333",
                      annotation_clip=False)


def _better_arrow(axis: Any, corner: str) -> None:
    """A short arrow above the axes saying which corner is better; it covers no data."""
    spec = {
        "upper left": ((0.17, 1.05), (0.01, 1.05), "left"),
        "upper right": ((0.83, 1.05), (0.99, 1.05), "right"),
        "lower left": ((0.17, 1.05), (0.01, 1.05), "left"),
        "lower right": ((0.83, 1.05), (0.99, 1.05), "right"),
    }.get(corner)
    if spec is None:
        return
    start, end, side = spec
    vertical = "↑" if corner.startswith("upper") else "↓"
    axis.annotate("", xy=end, xytext=start, xycoords="axes fraction", textcoords="axes fraction",
                  annotation_clip=False, arrowprops={"arrowstyle": "-|>", "color": "#555555", "lw": 0.8})
    tx = start[0] + (0.02 if side == "left" else -0.02)
    axis.text(tx, 1.05, f"better {vertical}", transform=axis.transAxes, ha="left" if side == "left" else "right",
              va="center", fontsize=7, color="#555555")


def _short_number(value: float) -> str:
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f"{int(value // 1_000_000)}M"
    if value >= 1000 and value % 1000 == 0:
        return f"{int(value // 1000)}k"
    return f"{value:g}"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def save(
    fig: Any,
    stem: str | Path,
    *,
    inputs: Sequence[str | Path] = (),
    role: str = "result",
    source: str | Path | None = None,
    project_root: str | Path | None = None,
    png_dpi: int = PNG_DPI,
) -> dict[str, str]:
    """Write ``<stem>.pdf`` and ``<stem>.png``, the facts file, and the provenance entry.

    ``inputs`` are the results files the figure was drawn from (recorded, not
    read). ``source`` is the analysis script (defaults to the running script).
    """
    import matplotlib.pyplot as plt

    stem_path = Path(stem)
    if stem_path.suffix.lower() in {".pdf", ".png", ".svg"}:
        stem_path = stem_path.with_suffix("")
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    facts = [f.as_dict() for f in _FACTS.pop(id(fig), [])]
    for f in facts:
        f.setdefault("legend", "none")
    payload = {"helper": HELPER, "figure": stem_path.name, "panels": facts, "inputs": [str(p) for p in inputs]}
    pdf = stem_path.with_suffix(".pdf")
    png = stem_path.with_suffix(".png")
    fig.savefig(pdf, metadata={"Creator": SOFTWARE})
    fig.savefig(
        png,
        dpi=png_dpi,
        metadata={"Software": SOFTWARE, f"{HELPER}:facts": json.dumps(payload, ensure_ascii=False)},
    )
    root = Path(project_root) if project_root is not None else _project_root_for(stem_path)
    facts_path = root / FACTS_DIR / stem_path.name / "facts.json"
    facts_path.parent.mkdir(parents=True, exist_ok=True)
    facts_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    plt.close(fig)
    written = {"pdf": str(pdf), "png": str(png), "facts": str(facts_path)}
    _register(root, stem_path, pdf, source, inputs, role, facts_path)
    return written


def _project_root_for(stem_path: Path) -> Path:
    for parent in [stem_path.resolve().parent, *stem_path.resolve().parents]:
        if (parent / "paper").is_dir() and (parent / "paper" / "main.tex").is_file():
            return parent
    return Path.cwd()


def _register(root: Path, stem_path: Path, pdf: Path, source: str | Path | None, inputs: Sequence[str | Path], role: str, facts_path: Path) -> None:
    script = Path(source) if source is not None else Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if script is None or not script.is_file():
        return
    try:
        from argus.verticals.research.figure_provenance import register_figure
    except ImportError:  # the project venv has no argus: the facts file is the record
        return
    try:
        register_figure(
            project_root=root,
            figure_id=stem_path.name,
            role=role,
            renderer=RENDERER,
            source_path=script,
            output_path=pdf,
            inputs=[Path(p) for p in inputs if Path(p).is_file()],
            render_metadata_path=facts_path,
            command=" ".join(sys.argv[:3]),
        )
    except Exception as exc:  # noqa: BLE001 - provenance is bookkeeping, the figure exists
        print(f"figure provenance not recorded: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# demo: python paper_charts.py [output_dir]
# ---------------------------------------------------------------------------
def _demo(out_dir: str = "/tmp") -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    lengths = ["4k", "8k", "16k", "32k"]
    acc = {
        "KIVI INT2": [[91.2, 89.8, 90.5], [84.1, 86.0, 85.2], [70.3, 72.1, 71.0], [35.4, 38.0, 36.1]],
        "RTN INT4": [[95.0, 95.6, 95.1], [94.3, 94.9, 94.0], [92.8, 93.5, 93.0], [90.1, 91.0, 90.4]],
        "Ours (2.25 bit)": [[95.4, 96.0, 95.7], [94.8, 95.3, 94.6], [93.6, 92.9, 93.4], [90.9, 91.6, 90.7]],
    }
    fig, _ = bars(lengths, acc, ours="Ours (2.25 bit)", xlabel="Context length", ylabel="RULER accuracy (%)",
                  reference=96.4, reference_label="BF16", two_column=True)
    written.extend(save(fig, out / "demo_bars", project_root=out).values())

    x = [1024, 2048, 4096, 8192, 16384, 32768]
    mem = {
        "BF16": [0.5, 1.0, 2.0, 4.0, 8.0, 16.0],
        "KIVI INT2": [0.07, 0.14, 0.27, 0.54, 1.08, 2.16],
        "Ours (2.25 bit)": [0.08, 0.15, 0.30, 0.60, 1.20, 2.40],
    }
    fig, _ = lines(x, mem, ours="Ours (2.25 bit)", xlabel="Sequence length (tokens)", ylabel="KV cache (GB)",
                   yscale="log", two_column=True)
    written.extend(save(fig, out / "demo_lines", project_root=out).values())

    fig, axes = grid(1, 2, column="double", two_column=True)
    bars(lengths, acc, ours="Ours (2.25 bit)", xlabel="Context length", ylabel="RULER accuracy (%)", ax=axes[0])
    lines(x, mem, ours="Ours (2.25 bit)", xlabel="Sequence length (tokens)", ylabel="KV cache (GB)", yscale="log", ax=axes[1])
    finish(fig)
    written.extend(save(fig, out / "demo_panels", project_root=out).values())

    fig, _ = dots(
        {"BF16": (16.0, 96.4), "RTN INT4": (4.0, 90.4, 0.4), "KIVI INT2": (2.0, 36.1, 1.2), "Ours (2.25 bit)": (2.25, 90.9, 0.5)},
        ours="Ours (2.25 bit)", xlabel="Bits per KV element", ylabel="RULER accuracy at 32k (%)", better="upper left",
        two_column=True,
    )
    written.extend(save(fig, out / "demo_dots", project_root=out).values())
    return written


if __name__ == "__main__":
    for path in _demo(sys.argv[1] if len(sys.argv) > 1 else "/tmp/paper_charts_demo"):
        print(path)
