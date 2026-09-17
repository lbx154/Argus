---
name: "Styling data figures for publication"
description: "Draw every data figure in a paper through the built-in chart route: an ECharts option rendered to vector by the browser renderer, one shared paper theme, uncertainty from the data. Use this for accuracy, error, latency, and ablation plots, bar and line charts, and heatmaps."
---

# Styling data figures for publication

## The route

Data figures are ECharts charts. The editable source is a JSON option under
`paper/figures/src/<id>/` with the page that hosts it; the export the
manuscript includes is the vector PDF (and SVG) under `paper/figures/<id>.*`,
rendered by the vertical's browser renderer with embedded TrueType fonts. One
helper does the whole trip and applies the paper theme, so every chart in a
paper shares typography, palette and emphasis without a hand-kept style file:

```bash
HELPER=$(find "$ARGUS_SKILL_HOME" . -name echarts_figure.py -path '*figure_spec_scripts*' 2>/dev/null | head -1)
python "$HELPER" --spec paper/analysis/fig2.option.json --id fig2_results \
  --width-mm 140 --height-mm 60 --font-pt 8 --ours "Our method"
```

`--spec` is a plain ECharts option (`xAxis`, `yAxis`, `series`, ...) written by
the analysis script from the results files, or `{"option": ..., "ours": ...,
"width_mm": ..., "height_mm": ..., "font_pt": ...}`. From Python:

```python
from echarts_figure import render_chart  # beside this skill in figure_spec_scripts/
render_chart(option, figure_id="fig2_results", width_mm=140, height_mm=60, ours="Our method")
```

Prerequisites, installed in the project environment when missing:
`pip install playwright && python -m playwright install chromium`. The ECharts
bundle is a local file: `--echarts-js`, `ARGUS_ECHARTS_JS`, an existing
`node_modules/echarts`, or a one-time pinned download into
`paper/figures/src/vendor/`. A rendering failure is an environment fault to
repair, never a reason to draw the chart by another means or to hand-draw
measured curves as shapes.

## What the theme does, and what you still decide

The helper sets: no animation, white background, a colour-blind-safe palette,
thin dark axes and light grid lines, a sans typeface at the requested point
size, legend at the top, the series named by `--ours` in vermilion with a
heavier line and larger markers, the rest in palette order. It turns an
`error` list on a series (a half-width per point, or `[lo, hi]` bounds) into
whiskers drawn from the data. Anything you set in the option wins over the
theme.

You decide, per figure:

- **Physical size.** State it in millimetres for the float it fills: a full
  text-width figure at 140 mm, two panels at 66 mm each, three at 44 mm.
  Height around 0.6 of width for curves, taller for bars with many groups.
  Fonts 7 to 9 pt at that size; never rescale a figure so type shrinks.
- **Axes.** Name every axis with its unit; use `type: "log"` when the data
  span decades; annotate an exact zero or use a signed-log scale instead of
  plotting a substituted sentinel for zero or a missing point.
- **Uncertainty.** Wherever runs were repeated, give the series an `error`
  list (standard error or standard deviation, said in the caption). A single
  run has no error bar and the caption says so.
- **Comparison.** Baselines in muted palette colours, the method under test
  emphasised once; the same series colour for the same method across figures.
- **Legend and labels.** Legend clear of the data and the title; panel letters
  in the caption, not baked into the image unless the panels are one export.

## Inspect before including

Open the exported PDF at publication size. Check that the type is readable,
the legend covers nothing, the whiskers are visible where the data has them,
and the axis names sit clear of tick labels. `python -m
argus.verticals.research.figure_lint` reports missing files, raster exports,
non-embedded fonts and figures produced outside this route. Keep
`paper/figures/src/<id>/option.json` under version control: it is the figure.

## What does not belong here

- Conceptual, method, pipeline or architecture figures: those follow
  `paper-framework-figure-studio.md` (PPT Master, Method D with Method B as
  the fallback). A data panel inside such a figure is an ECharts component
  composed into the native PPT, as `academic-vector-figures.md` describes.
- Plotting libraries and hand-authored SVG for data: not routes in this
  vertical. The lint lists any such export or script as a defect the Reviewer
  returns.
