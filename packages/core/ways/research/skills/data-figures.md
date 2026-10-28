# Drawing data figures for publication

Use this for every figure drawn from data: accuracy, latency and ablation
plots, bar and line charts, scatter plots, heatmaps. Conceptual, method,
architecture and teaser figures follow `method-figures.md` instead; there is
nothing here to plot until experiments have produced raw rows.

## Why one set of rules

Data figures drawn independently often miss the care of a finished paper:
default blue and orange, rainbow colormaps, wrong font sizes, Type 3 fonts,
colours that collapse under colour-blind simulation. Every past figure that
looked wrong was wrong in the same drawing choices: hand-picked colours, a
framed legend on the data, bars starting at 30, seeds averaged into one bar, an
in-plot title. Make those choices once, the same way for every figure, in the
project's own plotting code (matplotlib, optionally with SciencePlots as the
base style; whichever you pick, every chart in the paper uses it).

## The rules

- **Data in, picture out.** The plotting script reads the raw per-seed rows the
  runs wrote, never a summary someone typed or a number copied into the script.
  It draws the mean with the spread over repeats as error bars or bands, and
  the caption says how many runs each point averages and what the error bar is
  (standard deviation over seeds, a 95% interval).
- **Ours stands out.** The proposed method takes one reserved accent colour, a
  black edge or a heavy solid line, and is drawn on top. Baselines take distinct
  palette colours with distinct markers and dash patterns, so the figure still
  reads in greyscale. Use a colour-blind-safe palette such as Okabe–Ito
  (`#0072B2 #E69F00 #009E73 #CC79A7 #56B4E9 #F0E442 #000000`, accent
  `#D55E00`), and give each method the same colour in every figure.
- **Honest axes.** Bars start at zero, because a bar encodes its value by
  length and a higher origin exaggerates every difference; a chart that must
  start higher says so in its caption. Powers of two get a log2 axis. A zero on
  a log axis is stated (annotated, or a symlog scale), never plotted as a
  substituted small value. No dual axes, no 3D, no silently truncated axis.
- **One legend, outside the data**, shared by panels that tell one story; no
  in-plot title, since the caption is where a paper names its figures.
- **Sized for its float.** Draw at the final width: `\columnwidth` for a
  single-column `figure`, `\textwidth` for a full-width `figure*` (print them
  from the venue template with `\showthe\columnwidth`). Labels are about the
  size of the paper's footnotes at that width. A teaser or pipeline overview is
  a full-width `figure*`; sub-module and detail plots stay single-column.
- **Vector, with TrueType fonts.** Save a PDF for the manuscript and a PNG at
  manuscript width for looking at. Matplotlib's default embeds Type 3 fonts,
  which camera-ready checks reject.
- **A record beside it.** Each figure has `paper/figures/<stem>.source.json`:
  `{"source": "<the script that draws it>", "data": ["<results files or folders it reads>"]}`,
  paths relative to the workspace. Redraw and the record goes with the figure;
  `checks/paper.mjs` reports a missing record, a figure older than its source
  or data, Type 3 fonts in a PDF, and a PNG exported by matplotlib where a
  vector PDF belongs.

A minimal setup that keeps these rules:

```python
import json
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,  # TrueType, not Type 3
    "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
})

def save(fig, stem, source, data):
    fig.savefig(f"paper/figures/{stem}.pdf")
    fig.savefig(f"paper/figures/{stem}.png", dpi=200)
    with open(f"paper/figures/{stem}.source.json", "w") as f:
        json.dump({"source": source, "data": data}, f, indent=2)

rows = np.asarray(per_seed)  # conditions x seeds, read from the raw results
mean, spread = rows.mean(axis=1), rows.std(axis=1, ddof=1)
```

## Drawing and checking

1. **Choose the chart from the estimand.** Bars for a few categories, lines for
   a swept variable, dots for a two-metric trade-off, panels in one figure when
   they tell one story. Each figure supports one claim of the draft; plotting
   every metric dilutes the one the reader should take away.
2. **Learn composition from real papers.** Before locking layouts, look at how
   two or three open-access papers in the area draw their results
   (`paper-drafting.md` describes reading exemplars): panel count, axis
   conventions, how they emphasise their own method, caption phrasing. Match
   the conventions, not the data.
3. **Look at the PNG at final size** before the figure enters the draft:
   clipping, crowded ticks, labels covering points, panels that compare
   methods on different y-scales. Repair in the data or the plotting call, not
   by post-editing the PDF.
4. **Run `checks/paper.mjs`** once the figure is in the manuscript (see the
   README).

A chart the rules do not name (a heatmap, a violin plot) keeps the same
conventions by hand: the same palette and accent, legend outside, axis from
zero for anything encoded by length, no title.

## Readable heatmap annotations

Choose annotation text from the cell's actual displayed colour, including its
transparency, not from a rule such as `value >= 3`: a value of 3 can sit in a
pale cell or a dark one depending on the colormap and normalization. Composite
the cell colour over the white page and pick the text colour with more
contrast by sRGB relative luminance:

```python
def text_color(rgba, alpha=1.0):
    a = rgba[3] * alpha
    rgb = np.asarray(rgba[:3]) * a + (1 - a)  # over the white page
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    luminance = linear @ [0.2126, 0.7152, 0.0722]
    return "black" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "white"

# im is the AxesImage from imshow; the cell's colour is im.cmap(im.norm(value)).
ax.text(col, row, f"{value:g}", ha="center", va="center", color=text_color(im.cmap(im.norm(value))))
```

If the colour already includes its applied opacity, do not pass `alpha` again.
Keep the existing handling of missing or masked values. A local text-colour
repair does not reopen the figure's composition.
