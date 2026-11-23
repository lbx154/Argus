# Corpus report

Every constant in `scripts/cvprfig/style.py` and `palettes.py` traces to a
measurement in this document. Where a number here disagrees with intuition,
the number wins; where the corpus does not support a rule, the rule was cut,
and the cuts are recorded at the bottom so nobody re-adds them.

## What was measured

| | |
|---|---|
| Papers | 46 with complete arXiv LaTeX source |
| Groups | Tsinghua MARS (9), Wenzhao Zheng's group (14), Megvii (23) |
| Venues | CVPR, ICCV, ECCV/LNCS, NeurIPS, ICLR, AAAI, ICRA/IROS, ACM MM, IEEE journals |
| Figure environments | 292 |
| Table environments | 298 |
| Figure PDFs analysed | 349 (137 classified as diagrams, 184 photo panels, 28 plots) |
| Graphics refs resolved to a file with a known display width | 185 |

Extraction: `\begin{figure}` / `\begin{table}` environments parsed with a
brace-matching scanner after `\input`/`\include` inlining and comment
stripping; PDF interiors read with PyMuPDF's `get_drawings()` and
`get_text("dict")`, so fills, stroke colours, stroke widths, font names and
glyph sizes come from the vector content rather than from a rendering.

Classification of a PDF as a *diagram* (rather than a photo panel or a plot):
fewer than 55% of the canvas covered by raster, or more than 400 characters of
text; and not (more than 200 vector objects with fewer than 250 characters),
which separates matplotlib output. The buckets are approximate and a handful
of plots leak into the diagram set; where that matters it is called out.

## Finding 1 — nobody draws in TikZ

**1 of 290** figure environments contains a `tikzpicture` — a single ablation
bar chart in PETR. Every other figure, and every architecture figure without
exception, places external artwork with `\includegraphics`: 224 PDF, 135 PNG,
48 JPG.

**86% of figures are a single `\includegraphics`**; only 15 figures use
`subfigure`/`subfloat` at all. Composition happens inside the drawing tool and
arrives as one file.

(The earlier draft of this report said "0 of 292". That came from a scanner
whose `\input` resolution differed slightly from the one that ships; the
shipped reader finds 290 figure environments and one `tikzpicture`. The
substance is unchanged and the corrected number is the one to quote.)

The fonts embedded in that artwork say which tool:

| Font | Characters | What it implies |
|---|---|---|
| Times New Roman PS MT | 14,382 | Office with Times set by hand |
| Nimbus Roman No9 L | 8,101 | figure compiled through pdfTeX |
| Times New Roman PS Bold MT | 5,540 | |
| CMR10 / CMBX12 | 5,863 | LaTeX-generated panel |
| Arial MT | 3,112 | Office / Illustrator default sans |
| Calibri, Cambria Math | 2,719 | **PowerPoint or Visio**, unambiguously |
| Helvetica Neue | 1,383 | macOS Keynote / Illustrator |
| DejaVu Sans | 854 | matplotlib |
| Segoe Print | 354 | Windows |

Serif carries **71%** of diagram characters against 15% for the sans faces —
figures match the body font of the paper they sit in. Hence `DEFAULT_FONT =
"times"`.

*Consequence for this project:* generating a native SVG/VSDX/PPTX with an
Office palette is not a stylistic choice, it is a reconstruction of the
actual production pipeline. Emitting TikZ would be the anomaly.

## Finding 2 — the palette is three colour pickers

101 colours appear in four or more distinct figures. Sorted by hue and
lightness they resolve into three coherent systems, which is what
`palettes.py` encodes:

- **Office 2013+ theme accents** — `#DEEBF7 #BDD7EE #9DC3E6 #5B9BD5 #2E75B6
  #1F4E79` is the Blue Accent 5 ladder verbatim; likewise Green Accent 6
  (`#E2F0D9 … #70AD47 … #548235`), Gold Accent 4 (`#FFF2CC … #FFC000 …`),
  Orange Accent 2 (`#FBE5D6 … #ED7D31 …`) and the grey ladder (`#F2F2F2
  #D9D9D9 #BFBFBF #A6A6A6 #7F7F7F`).
- **diagrams.net stock styles** — always as a fill/stroke *pair*: `#DAE8FC`
  with `#6C8EBF`, `#D5E8D4` with `#82B366`, `#FFE6CC` with `#D79B00`,
  `#F8CECC` with `#B85450`, `#E1D5E7` with `#9673A6`.
- **matplotlib tab10** — `#1F77B4 #FF7F0E #2CA02C #D62728 …`, confined to
  plot panels.

Hue share of coloured fill area: blue 25%, green 25%, orange 22%, red 10%,
teal 8.5%, purple 3.7%, gold 3.5%. The blue/green/orange triad carries 72%,
which is why the default `ROLES` table reaches for those first.

Distinct saturated families in one figure: **median 5**, p75 9, p90 14. The
auditor warns above 6.

Outlines are a different story from fills. **45% of stroke length is pure
black**, and most of the remainder is the grey ladder (`#B0B0B0 #D9D9D9
#7F7F7F #A6A6A6`) or a dark navy (`#2F528F`, `#41719C`). Family-tinted
outlines are a clear minority, so `outline: ink` is the default and
`outline: match` is opt-in.

## Finding 3 — what the type actually renders at

Canvas font sizes are meaningless on their own; what matters is the size
after the figure is scaled into its column. For the 185 graphics whose
`\includegraphics` width could be resolved, the scale factor
(display width ÷ canvas width) has **median 0.385**.

Applying each figure's own scale factor:

| Rendered glyph size | Share |
|---|---|
| < 5.0 pt | 29% |
| 5.0 – 6.0 pt | 17% |
| 6.0 – 7.5 pt | 24% |
| 7.5 – 9.5 pt | 24% |
| > 9.5 pt | 6% |

Median **6.0 pt**, quartiles 4.5 and 7.5. The median *smallest* glyph in a
figure is **5.4 pt**.

So published practice runs a body tier near 6.5–7 pt with annotation dipping
to 5. v2.0 set `MIN_RENDERED_PT = 5.0` (the 29th percentile) and
`WARN_RENDERED_PT = 5.6`.

**v3 recalibration.** Matching the corpus distribution turned out to be the
wrong target: figures built at 7.0 pt body printed at 5.8–7.3 pt and readers
found them small. The corpus median describes what gets published, not what
reads well. v3 re-measured the figures users actually hold up as the model —
the template papers of a driving-occupancy project — the same way (glyph size
in the figure PDF × the `\includegraphics` scale):

| Paper | Figure | Tool | p10 | median | p90 |
|---|---|---|---|---|---|
| SparseWorld | overview | Visio | 6.4 | 7.5 | 7.5 |
| SparseWorld | comparison | Visio | 6.1 | 6.6 | 7.5 |
| SparseWorld | visualisation | PowerPoint | 6.4 | 7.4 | 9.2 |
| Occ3D | data pipeline | PowerPoint | 4.9 | 8.2 | 8.2 |
| Occ3D | model | PowerPoint | 4.9 | 5.7 | 6.5 |
| BEVDepth | backbone | Keynote | 8.6 | 8.6 | 8.6 |
| BEVDepth | unprojection | Keynote | 6.6 | 6.6 | 6.6 |
| OccFusion | teaser | PowerPoint | 6.4 | 6.4 | 8.2 |
| BEVerse | framework | draw.io | 5.3 | 5.3 | 5.9 |

The diagrams users like sit at a 7.5 pt median. v3 therefore sets the body
tier at **7.5 pt**, stage titles at 8.5 pt, `MIN_RENDERED_PT = 6.0`,
`WARN_RENDERED_PT = 6.5`, and module names at no less than 7.0 pt. The
Supervisor-Skills figure checklist (HKUST) asks for 8 pt after scaling; the
stage titles meet it and module names approach it.

Rendered stroke widths, same scaling applied: modes at **0.19 pt** (13%),
**0.36 pt** (24%), **0.45 pt** (8%) and **0.80 pt** (5%). v1 of this project
guessed 0.30/0.65/0.80/1.10 — roughly 2× too heavy — and `STROKE` was
recalibrated to the measured modes.

## Finding 4 — framework figures contain real data

**78 of 137 diagrams (57%) embed at least one raster image.** When they do,
the median count is **11** and they cover about **23%** of the canvas.

Where those rasters sit, by horizontal third: **left 45%**, centre 28%, right
27%. And **66% of raster-bearing diagrams have images at both the far left and
the far right** — the input→prediction pattern, with the architecture in
between.

Median raster block aspect is 1.33 (4:3 camera frames), quartiles 1.00 and
1.69.

This is what `shape: image`, `imagegrid` and `cameraring` exist for, why
`image` inherits its aspect from the real file, and why `check_real_content`
nudges a box-only pipeline figure.

## Finding 5 — layout and placement

| | |
|---|---|
| Figures per paper | median 6 (Mars 4, Megvii 6, Wenzhao 6) |
| Tables per paper | median 6 |
| `figure*` (full page width) | 44% |
| `table*` | 25% |
| Placement specifier | `t` 48%, `t!` 16%, `!t` 11% — 75% top-anchored |
| `\includegraphics` width | 1.0/0.99/0.98 `\linewidth` dominate |
| Figure caption length | median 39 words, p90 84 |
| Table caption length | median 19 words |
| booktabs rules | 70% of tables |
| Diagram canvas aspect (w/h) | median 1.82, p10 0.96, p90 3.35 |

The first figure of a paper — the teaser — sits at 6.9% into the document,
is a single graphic in 41 of 46 papers, and carries a **49-word** caption.
Only 10 of 46 make it full width.

## Finding 6 — prose, for the writing skills

| | Median |
|---|---|
| Sections | 6 — `Introduction > Related Work > Method > Experiments > Conclusion` in 16 of 46 |
| Abstract | 178 words, 9 sentences |
| Body | 4,352 words |
| Sentence length | 19 words; p90 33 |
| First person (`we`/`our`/`us`) | 20 per 1,000 words |
| Hedges (`may`, `might`, `could`, `suggests that`…) | 0.96 per 1,000 words |
| Boosters (`significantly`, `remarkably`…) | 0.69 per 1,000 words |
| Equations | 8 |
| Algorithm environments | 2 |
| `\cite` calls | 71 |
| "novel" | 3 |
| "state-of-the-art" | 5 |

The hedge rate is the striking one: under one hedge per thousand words. These
papers assert. That number is the empirical basis for the defensive-writing
audit in `cvpr-skills`.

## Rules that were tested and cut

Recorded so they do not get re-added from intuition.

- **"84% of fill area is neutral, so keep figures mostly grey."** True of raw
  fill area, but only because the page-sized white backdrop is itself a fill.
  Measured the way an auditor would measure it — coloured share of *box* fill
  area, page excluded — the corpus median is **87% coloured** (p25 0.66, p75
  0.99). A "colour flood" check calibrated on the first number would fire on
  almost every published figure. Cut.
- **"Coloured fills stay on the palest rungs."** Deep-tint share of coloured
  area: median 0.30, but p75 0.85. Too wide to threshold. Cut.
- **Minimum type of 5.5 pt** (v1). The corpus puts 29% of glyphs below 5.0 and
  the median per-figure minimum at 5.4, so 5.5 as a hard floor would reject
  half the reference set. Softened to a 5.0 error / 5.6 warning.

## Reproducing

The scan scripts are not shipped in this repository — they read arXiv sources
that are not ours to redistribute. They are ~200 lines of PyMuPDF and `ast`,
and the method is described precisely enough above to rebuild: inline
`\input`, strip comments, brace-match the float environments, then read
`get_drawings()` and `get_text("dict")` from page 1 of each referenced PDF and
weight fills by rectangle area and strokes by path length.
