"""Small physical-unit canvas for editable, publication-size vector figures.

The model owns composition and scientific content. This module supplies a
consistent drawing surface, real math typography, and checked vector exports;
it deliberately does not turn a list of modules into a generic box diagram.
"""

from __future__ import annotations

import math
import tempfile
import warnings
from pathlib import Path
from typing import Any, Sequence

INK = "#28344A"
MUTED = "#69768A"
RULE = "#ACB6C4"
# Restrained NPG-inspired scientific colors: use at most two accents per figure.
BLUE = "#3C5488"
TEAL = "#008F7A"
CORAL = "#C17664"
LIGHT_BLUE = "#EFF2F7"
LIGHT_TEAL = "#EEF6F3"
LIGHT_CORAL = "#FAF1EE"


class FigureCanvas:
    """Draw in typographic points, with the origin at the upper-left corner.

    A 396-point canvas is exactly 5.5 inches wide. Font sizes and line widths
    are consequently their actual sizes in a manuscript included at that width.
    Use ``axes`` for domain-specific shapes or plots not covered by the helpers.
    """

    def __init__(
        self,
        width: float = 396,
        height: float = 216,
        *,
        font: str = "DejaVu Sans",
        font_size: float = 8.3,
    ) -> None:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.font_manager import FontProperties, findfont

        if not all(math.isfinite(v) and v > 0 for v in (width, height, font_size)):
            raise ValueError("canvas dimensions and font size must be finite and positive")
        if font_size < 8:
            raise ValueError("ordinary labels must be at least 8 pt at publication size")
        findfont(FontProperties(family=font), fallback_to_default=False)
        self.width, self.height = width, height
        self.font, self.font_size = font, font_size
        self.figure = Figure(figsize=(width / 72, height / 72), dpi=144, facecolor="white")
        FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_axes((0, 0, 1, 1))
        self.axes.set(xlim=(0, width), ylim=(height, 0))
        self.axes.set_axis_off()
        self._label_regions: list[tuple[Any, tuple[float, float, float, float]]] = []

    def text(
        self, x: float, y: float, value: str, *, size: float | None = None,
        color: str = INK, weight: str = "normal", ha: str = "left", va: str = "center",
        **kwargs: Any,
    ) -> Any:
        size = self.font_size if size is None else size
        if not math.isfinite(size) or size < 8:
            raise ValueError("ordinary labels must be at least 8 pt at publication size")
        label = self.axes.text(
            x, y, value, fontsize=size, fontfamily=self.font, math_fontfamily="stix",
            color=color, weight=weight, ha=ha, va=va, linespacing=1.2,
            zorder=4, **kwargs,
        )
        return label

    def panel(self, x: float, y: float, letter: str, title: str) -> None:
        self.text(x, y, f"({letter})", size=9, weight="bold")
        self.text(x + 17, y, title, size=9)

    def box(
        self, x: float, y: float, width: float, height: float, label: str = "", *,
        fill: str = "white", edge: str = RULE, radius: float = 1.8,
        linewidth: float = .65, padding: float = 4, color: str = INK,
        weight: str = "normal",
    ) -> Any:
        from matplotlib.patches import FancyBboxPatch

        patch = FancyBboxPatch(
            (x, y), width, height, boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=fill, edgecolor=edge, linewidth=linewidth, zorder=1,
        )
        self.axes.add_patch(patch)
        if label:
            text = self.text(x + width / 2, y + height / 2, label, ha="center", color=color, weight=weight)
            self._label_regions.append((text, (x + padding, y + padding, x + width - padding, y + height - padding)))
        return patch

    def line(
        self, points: Sequence[tuple[float, float]], *, color: str = RULE,
        width: float = .65, dashed: bool = False, **kwargs: Any,
    ) -> Any:
        xs, ys = zip(*points, strict=True)
        return self.axes.plot(
            xs, ys, color=color, linewidth=width, linestyle=(0, (3, 2)) if dashed else "-",
            solid_capstyle="round", solid_joinstyle="round", zorder=2, **kwargs,
        )[0]

    def arrow(
        self, points: Sequence[tuple[float, float]], *, color: str = MUTED,
        width: float = .7, dashed: bool = False, head: float = 5,
    ) -> Any:
        from matplotlib.patches import FancyArrowPatch
        from matplotlib.path import Path as MplPath

        if len(points) < 2:
            raise ValueError("an arrow needs at least two points")
        patch = FancyArrowPatch(
            path=MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1)),
            arrowstyle="-|>", mutation_scale=head, linewidth=width,
            linestyle=(0, (3, 2)) if dashed else "-", color=color,
            capstyle="round", joinstyle="round", zorder=2,
        )
        self.axes.add_patch(patch)
        return patch

    def _bounds(self, label: Any, renderer: Any) -> tuple[float, float, float, float]:
        bounds = label.get_window_extent(renderer=renderer)
        scale = 72 / self.figure.dpi
        return bounds.x0 * scale, self.height - bounds.y1 * scale, bounds.x1 * scale, self.height - bounds.y0 * scale

    def validate(self) -> None:
        """Check fonts, label sizes, clipping, label collisions, and card padding.

        This catches technical defects, not scientific or aesthetic mistakes.
        Inspect the final composition and its manuscript placement separately.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.figure.canvas.draw()
        missing = [str(w.message) for w in caught if "Glyph" in str(w.message) and "missing" in str(w.message)]
        if missing:
            raise ValueError("font lacks required glyphs: " + "; ".join(dict.fromkeys(missing)))
        renderer = self.figure.canvas.get_renderer()
        bounds: list[tuple[Any, tuple[float, float, float, float]]] = []
        for label in self.axes.texts:
            if not label.get_visible() or not label.get_text():
                continue
            if label.get_fontsize() < 8:
                raise ValueError("ordinary labels must be at least 8 pt at publication size")
            x0, y0, x1, y1 = self._bounds(label, renderer)
            if x0 < -.3 or y0 < -.3 or x1 > self.width + .3 or y1 > self.height + .3:
                raise ValueError(f"label leaves the canvas: {label.get_text()!r}")
            bounds.append((label, (x0, y0, x1, y1)))
        for i, (label, (x0, y0, x1, y1)) in enumerate(bounds):
            for other, (left, top, right, bottom) in bounds[i + 1:]:
                if min(x1, right) - max(x0, left) > .5 and min(y1, bottom) - max(y0, top) > .5:
                    raise ValueError(f"labels overlap: {label.get_text()!r} and {other.get_text()!r}")
        for label, (left, top, right, bottom) in self._label_regions:
            x0, y0, x1, y1 = self._bounds(label, renderer)
            if x0 < left - .3 or y0 < top - .3 or x1 > right + .3 or y1 > bottom + .3:
                raise ValueError(f"enlarge the box or shorten its label; padding is lost: {label.get_text()!r}")

    def export(self, stem: Path | str, *, dpi: int = 240) -> dict[str, Path]:
        """Validate and write matching SVG, vector PDF, and PNG previews."""
        from matplotlib import rc_context

        self.validate()
        stem = Path(stem)
        if stem.suffix:
            raise ValueError("export requires a filename stem without an extension")
        stem.parent.mkdir(parents=True, exist_ok=True)
        targets = {kind: stem.with_suffix(f".{kind}") for kind in ("svg", "pdf", "png")}
        with tempfile.TemporaryDirectory(prefix=".figure-", dir=stem.parent) as scratch:
            with rc_context({"svg.fonttype": "none", "pdf.fonttype": 42, "pdf.use14corefonts": False}):
                for kind in targets:
                    self.figure.savefig(Path(scratch) / f"figure.{kind}", format=kind, dpi=dpi, facecolor="white")
            # No final export is replaced until every format renders successfully.
            for kind, target in targets.items():
                (Path(scratch) / f"figure.{kind}").replace(target)
        return targets
