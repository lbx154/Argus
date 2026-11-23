# Getting the figure into the paper at the right size

## The sizing rule

The engine lays out in **final rendered points**. `figure.venue` +
`figure.width` set the canvas to the exact column width of the venue, so:

```latex
\includegraphics[width=\linewidth]{figures/overview.pdf}
```

renders at 100%, and a `size: 7.5` label is 7.5 pt on the page.

Everything follows from that:

- Want the figure at 80% of the column? Set `width_frac: 0.8` in the spec and
  keep `width=\linewidth` in LaTeX. Do **not** write
  `width=0.8\linewidth` — that shrinks 7.5 pt type to 6.0 pt.
- Overflowing? The renderer scales the whole canvas down and *tells you the
  effective point size*. Under 6.0 pt it raises an error, under 6.5 pt a
  warning, and module names must stay at 7.0 pt or more. The fix is to shorten
  labels, drop a stage, or split the figure — not to accept the shrink.

## Column widths, verified

| Venue | `\columnwidth` | `\textwidth` |
|---|---|---|
| CVPR / ICCV / WACV | 237.13 pt | 496.85 pt |
| ECCV (LNCS, single column) | 347.12 pt | 347.12 pt |
| NeurIPS | 397.48 pt | 397.48 pt |
| ICLR | 397.48 pt | 397.48 pt |
| ICML | 234.88 pt | 487.82 pt |
| AAAI | 238.49 pt | 504.00 pt |
| ACL / EMNLP | 219.08 pt | 455.24 pt |

Check yours rather than trusting the table: put
`\the\columnwidth\ \the\textwidth` in the document and compile.

## Float placement

```latex
% full width, top of page
\begin{figure*}[t]
  \centering
  \includegraphics[width=\linewidth]{figures/overview.pdf}
  \caption{\textbf{Overview of X.} <claim>. <parts, left to right>.}
  \label{fig:overview}
\end{figure*}

% single column teaser, top of page 1
\begin{figure}[t]
  \centering
  \includegraphics[width=\linewidth]{figures/teaser.pdf}
  \caption{...}
  \label{fig:teaser}
\end{figure}
```

- Page-1 teasers go in `figure` at `[t]` and are referenced from the first
  paragraph of the introduction.
- Framework figures go in `figure*` at `[t]`, placed in the source *before* the
  method section that discusses them.
- Never `[H]`. Never `\vspace` hacks to force placement; fix the figure's
  height budget instead.

## Caption conventions

- Bold lead-in stating the claim, then the parts in reading order, then what
  the colours mean, then **every symbol the figure shows that it does not
  spell out** — in the order the eye meets them. Badges (`×L`, `×N`) and
  operators (⊕, ⊗, ⓒ, `[·;·]`) are symbols too. See
  [symbols-and-captions.md](symbols-and-captions.md).
- Give the auditor the caption — `caption:` in the spec, or
  `caption_tex: sec/3_method.tex#fig:overview` to read it out of the paper —
  and it checks that each symbol marked `defined_in: caption` is really there.
- Keep it under ~4 lines for a `figure`, ~5 for a `figure*` (corpus median
  39 words, 90th percentile 84).
- **Recompile and check the page count after completing the definitions.**
  One more caption line in a `figure*` moves the text below by a line; in a
  single-column figure it can push the rest of the column. If the paper runs
  over, move definitions into the figure as words ("World canvas $c_s$")
  rather than cutting them.
- Reference it as `Fig.~\ref{fig:overview}` (CVPR/ICCV style) or
  `Figure~\ref{...}` (NeurIPS/ICLR style) — follow the venue's own template.

## Exporting the PDF without losing quality

The figure PDF must be **lossless**. Pictures embedded at 4000 ppi have come
out at 298 ppi JPEG because LibreOffice's default PDF filter downsamples to
300 dpi and recompresses. So:

- `render.py` converts SVG→PDF with LibreOffice's lossless options
  (`ReduceImageResolution=false`, `UseLosslessCompression=true`,
  `Quality=100`) and audits the result;
- once the PPTX has been edited by hand, export **from the PPTX** with
  `python3 scripts/export_pptx.py fig.pptx --width 496.8` (LibreOffice, or
  `--via keynote` on a Mac), which runs `audit_pdf.py` afterwards;
- from WPS, choose the highest picture quality — WPS keeps the resolution but
  still writes JPEG; from Keynote choose Image Quality **Best**;
- always finish with `python3 scripts/audit_pdf.py fig.pdf --width 496.8`.

The underlying command, if you need it elsewhere:

```bash
soffice --headless --convert-to 'pdf:impress_pdf_Export:{"ReduceImageResolution":{"type":"boolean","value":"false"},"UseLosslessCompression":{"type":"boolean","value":"true"},"Quality":{"type":"long","value":"100"}}' fig.pptx
```

(use `draw_pdf_Export` for an SVG). Application differences are tabulated in
[app-compatibility.md](app-compatibility.md).

## Embedded crops: enough pixels, losslessly

A crop must carry **600 ppi at its printed size** (300 is the error floor):
pixels wide ≥ printed width in points / 72 × 600. A crop printed 100 pt wide
needs about 830 px. Keep occupancy renders, masks and plots as PNG — JPEG
smears their hard edges. The auditor computes this for every `src:`
(`image-low-ppi`, `image-soft`), and `audit_pptx.py` / `audit_pdf.py` do the
same for the shipped files. To save space, crop to what is shown rather than
lowering the resolution.

## Reproducibility

Keep the spec next to the figure and commit both:

```
figures/
  overview.pdf          <- what LaTeX includes
  overview.svg
  src/overview.yaml     <- the spec
  src/crops/*.png       <- the real data panels
```

Regenerate everything with one command:

```bash
for f in figures/src/*.yaml; do
  python3 scripts/render.py "$f" -o "figures/$(basename "${f%.yaml}")" -f svg,pdf
done
```

Once the PPTX has been edited by hand it becomes the source: keep it under
`figures/`, export the PDF from it, and stop regenerating that figure from the
spec ([after-handover.md](after-handover.md)).

Camera-ready checks, in order:

1. `python3 scripts/validate.py spec.yaml --svg out/fig.svg --strict`
2. after any hand edit: `python3 scripts/audit_pptx.py fig.pptx --width <w> --spec spec.yaml`
3. `python3 scripts/audit_pdf.py fig.pdf --width <w>` — type, image ppi,
   JPEG, embedded fonts.
4. View the compiled paper at 100% and read every label.
5. Print one page in greyscale.
6. Recompile and confirm the page count after the final captions.
