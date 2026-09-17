---
name: "Styling data figures for publication"
description: "Give every data figure in a paper a consistent style suitable for a journal. Use this for accuracy, latency, and ablation plots, bar and line charts, or any data-driven paper figure. Covers a shared publication style (SciencePlots + colour-blind-safe palettes), sizing for the venue (single-column figure vs full-width figure*), redundant colour+marker encoding, highlighting the proposed method, correct PDF font embedding, and learning composition from open-access exemplar papers. Applies to any venue; column layout comes from the project's researched venue profile."
---

# Styling data figures for publication

## Why a shared style matters
Data figures drawn independently often miss the care of a finished conference
paper: default blue/orange, rainbow/`jet` colormaps, wrong font sizes, no font
embedding, and colours that collapse under colour-blind simulation. This skill gives every data
plot one shared style suitable for a journal via a small helper, `paper_chart_style.py`,
and a short set of composition rules learned from open-access papers. Conceptual
figures (teaser/pipeline/architecture) are not covered here — route those through
*Choosing how to draw a research figure*. This skill is only for
**data/metric/result plots that are legitimately scripted from local data**.

## When to use
- You are creating data-driven figures (curves, bars, scatter, heatmaps) for a
  paper from `paper/analysis/build_results.py` or similar.
- The figures currently look inconsistent, use a mismatched palette, or lack
  the visual finish of a conference paper.

## When to use another approach
- Conceptual/method/teaser/pipeline overview figures — use *Choosing how to draw
  a research figure* rather than disguising them as data plots.
- There is no local data to plot yet (run/analyze experiments first).

## How to draw the charts

The script names the data; the helper draws. Every past figure that looked
wrong was wrong in the drawing calls (hand-typed colours, a framed legend on
the data, bars from 30, seeds averaged into one bar, an in-plot title), so
`paper_charts.py` makes those calls the same way for every figure.

1. **Install the plotting stack in the project venv**: `pip install matplotlib
   seaborn SciencePlots`. A missing package is an environment error to repair,
   not permission to draw with plain matplotlib or hand-authored SVG.

2. **Copy both helper files next to the analysis scripts** (the project venv
   does not need `argus`):
   ```bash
   SRC=$(dirname "$(find "$ARGUS_SKILL_HOME" . -name paper_charts.py -path '*figure_spec_scripts*' 2>/dev/null | head -1)")
   mkdir -p paper/analysis && cp "$SRC/paper_charts.py" "$SRC/paper_chart_style.py" paper/analysis/
   ```

3. **Pass data and names; take the figure back.** A series is a list of
   numbers, or a list of lists when runs were repeated: the helper draws the
   mean with the spread (std over repeats) as error bars or bands and records
   the repeat count. Pass the per-seed rows, never their average.
   ```python
   from paper_charts import bars, lines, dots, grid, finish, save

   fig, ax = bars(["4k", "8k", "16k", "32k"], acc, ours="Ours",
                  xlabel="Context length", ylabel="RULER accuracy (%)",
                  reference=96.4, reference_label="BF16")      # dashed upper bound
   save(fig, "paper/figures/ruler_accuracy", inputs=["results/ruler.json"])

   fig, ax = lines(lengths, memory_gb, ours="Ours", yscale="log",
                   xlabel="Sequence length (tokens)", ylabel="KV cache (GB)")
   fig, ax = dots({"KIVI": (2.0, 36.1, 1.2), "Ours": (2.25, 90.9, 0.5)},
                  ours="Ours", xlabel="Bits per element", ylabel="Accuracy (%)", better="upper left")

   fig, axes = grid(1, 2, column="double")                    # panels share one legend
   bars(..., ax=axes[0]); lines(..., ax=axes[1]); finish(fig)
   save(fig, "paper/figures/main_results", inputs=[...])
   ```
   What the helper decides: the proposed method (`ours=`) takes the accent
   colour, a black edge or a heavy solid line and sits on top; baselines take
   distinct palette colours with distinct markers and dashes so the figure
   reads in greyscale; bars start at zero (a higher start needs
   `truncated_reason=`, recorded for the caption); one legend sits above the
   panels, never on the data; powers of two get a log2 axis; a zero on a log
   axis is an error to state, not a sentinel to plot; no in-plot title.
   `column="single"` or `"double"` sizes the figure for its LaTeX float; pass
   `two_column=` explicitly when `research/VENUE_PROFILE.json` is absent.

4. **`save` writes what the manuscript and the review need**: `<stem>.pdf`
   (TrueType fonts, embed this), `<stem>.png` at manuscript width (open it and
   look), `paper/figures/src/<stem>/facts.json` (what the figure encodes:
   repeats per series, axis origin, legend placement, missing points) and a
   provenance entry. `inputs=` names the results files the figure came from.

5. **Look at the PNG at final size** before the figure enters the draft:
   clipping, crowded ticks, labels covering dots, panels that compare methods
   on different y-scales. Repair in the data or the call, not by post-editing
   the PDF.

6. **Choose the chart from the estimand.** Bars for a few categories, lines
   for a swept variable, dots for a two-metric trade-off, `grid` when the
   panels tell one story. No 3-D, no dual axes, no truncated axis without a
   caption sentence. Each figure supports one claim of the draft; do not plot
   every metric.

7. **Learn composition from real papers.** Before locking layouts, run the
   *Learning from strong published papers* skill on two or three open-access
   papers in the area: panel count, axis conventions, how they emphasise their
   own method, caption phrasing. Match the conventions, not the data.

8. **`python -m argus.verticals.research.figure_lint --project-root .`** names
   the drawing decisions a script still makes by hand (colours, pinned legends,
   truncated bar axes, in-plot titles), missing or raster or Type 3 figures,
   and the facts the helper recorded (a bar axis not from zero, a legend
   inside). Run it before handing figures to the manuscript.

For a custom chart the helper has no verb for (heatmaps, violins), apply
`paper_charts.apply_style()` first and keep the same rules by hand: palette
from the helper, legend outside, axis from zero for lengths, no title.

## Readable heatmap annotations

Choose annotation text from the actual displayed cell color, including its
transparency, rather than a rule such as `value >= 3`. A value of 3 can occupy a
pale green cell or a dark cell depending on the colormap and normalization.
Keep the paper's colormap and use `contrast_text_color` from the shared helper:

```python
import numpy as np
from paper_chart_style import contrast_text_color

# Inside the existing annotation loop; im is the AxesImage returned by imshow.
rgba = im.cmap(im.norm(value))
opacity = im.get_alpha()
if opacity is not None and np.ndim(opacity):
    opacity = opacity[row, col]
text_color = contrast_text_color(
    rgba,
    alpha=opacity,
    background=ax.get_facecolor(),
    canvas=ax.figure.get_facecolor(),
)
ax.text(col, row, f"{value:g}", ha="center", va="center",
        color=text_color, alpha=1.0)
```

The helper composites the RGBA layers over the white paper surface and chooses
the higher-contrast black or white text using sRGB relative luminance. If an
artist's facecolor already contains its applied opacity, omit the extra
`alpha` argument to avoid applying it twice. Keep existing handling of missing
or masked values. Check annotation legibility during the existing export
inspection; a local text-color repair does not reopen the figure's composition.

## Notes
- Both helper files are dependency-light and self-contained; the copies in
  `paper/analysis/` are what your scripts import. Re-copy them if you upgrade.
- SciencePlots is mandatory for this route. A missing package is an environment
  error, not permission to fall back to the retired ad-hoc data-figure method.
- This skill styles data plots only. For conceptual/method figures, use
  *Choosing how to draw a research figure*, which considers the available renderers.
- Figure width must still agree with the LaTeX float type: teaser and the main
  pipeline/architecture overview are the full-width `figure*` floats; sub-module
  and detail plots stay single-column `figure` (the layout review flags an
  overview/teaser/pipeline graphic left in a single column).

## Alternative: the ECharts route

The same figure can come from an ECharts option rendered to vector by the
vertical's browser renderer, with the paper theme applied by
`figure_spec_scripts/echarts_figure.py` (palette, axes, typography at a stated
point size, the method under test emphasised, error bars from an `error`
list). The editable source is `paper/figures/src/<id>/option.json`; the export
is `paper/figures/<id>.pdf` with embedded TrueType fonts and a provenance
record:

```bash
HELPER=$(find "$ARGUS_SKILL_HOME" . -name echarts_figure.py -path '*figure_spec_scripts*' 2>/dev/null | head -1)
python "$HELPER" --spec paper/analysis/fig2.option.json --id fig2_results --width-mm 140 --height-mm 60 --font-pt 8 --ours "Our method"
```

Prerequisites when missing: `pip install playwright && python -m playwright
install chromium`; the ECharts bundle is located or fetched once into
`paper/figures/src/vendor/`. Use whichever route the analysis script already
speaks; keep one route per paper so the charts match. Neither route draws
conceptual, method or architecture figures: those follow
`paper-framework-figure-studio.md` (PPT Master).

