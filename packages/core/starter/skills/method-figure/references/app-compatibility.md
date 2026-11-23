# How the same PPTX behaves in each application

Measured, not assumed. Users of this skill edit in WPS (macOS), Keynote,
LibreOffice and PowerPoint, and a file that looks right in one can be wrong in
another. Columns marked *measured* were checked by exporting the same file and
reading the PDF back with PyMuPDF (LibreOffice 26, Keynote 14, macOS); *user*
means observed by a user on a real figure; *not tested* means exactly that.
PowerPoint was not available for measurement.

## Arrowheads

OOXML line ends have three sizes per axis (`sm`, `med`, `lg`). The applications
turn them into points differently:

| | head length = | `sm` / `med` / `lg` multiplier | source |
|---|---|---|---|
| LibreOffice | k × max(line width, **1.984 pt**) | 2 / 3 / 5 | measured |
| PowerPoint | taken as LibreOffice's, which emulates it | 2 / 3 / 5 | assumed |
| WPS (macOS) | k × line width | 2 / 3 / 5 | user |
| Keynote | k × line width | 2.62 / 4.31 / 7.75 | measured |

So for a 0.6 pt arrow the same `sm/sm` head is 3.97 pt in LibreOffice and
PowerPoint, 1.2 pt in WPS and 1.6 pt in Keynote. No preset is right in all
four. The engine therefore picks the preset for the application that will
**produce the paper PDF** — `figure: {pptx_app: office}` by default, since
`export_pptx.py` exports with LibreOffice — and draws exactly that head in
the SVG, so SVG, PPTX and the exported PDF agree. Set `pptx_app: wps` or
`pptx_app: keynote` if that application makes the final PDF. With
`pptx_heads: shape` the heads are drawn as triangles and look identical
everywhere, at the cost of not following a moved connector.

## The full matrix

| | PowerPoint | WPS (macOS) | Keynote | LibreOffice |
|---|---|---|---|---|
| bent arrow whose last run is shorter than its head | may tilt (assumed) | **tilts 45°** (user) | not affected | not affected (measured) |
| `baseline` sub/superscripts | kept | kept (user) | kept, shown at 67–69 % of the run; moved by about 1.07 × baseline × run size (measured) | kept, shown at 58.5 % of the run; moved by baseline × the run's **own** size, so an enlarged script drops further (measured) |
| glued `straightConnector1`/`bentConnector2/3` | native | native (user) | H-V-H kept; **rotated V-H-V re-routed** (measured) | kept exactly: 159 of 159 template routes, `tests/compat/lo_roundtrip.py` (measured) |
| custom-shape connection sites as literal coordinates | not tested | not tested | not tested | **misplaced** (measured) — the engine writes them as guides |
| slide with no `<p:bg>` | white | not tested | **black** (measured) — the engine sets white | white |
| font sizes | as written | as written | **whole points only**: 6.5 → 6 (measured) | as written |
| shadows / glow | vector | not tested | not tested | rasterised at 96–350 ppi |
| theme shadow (`effectRef idx>0`) under an empty `<a:effectLst/>` | hidden (spec) | not tested | **hidden** (measured) | **drawn**, 139 of 139 shapes, as 97–313 ppi grey pictures (measured) |
| PDF export, pictures | not tested | resolution kept, **JPEG** (user) | kept at Best (measured) | **300 dpi JPEG by default**; lossless with `export_pptx.py` (measured) |
| text box narrower than its text | grows (`spAutoFit`) | not tested | shrinks text — the engine adds slack | grows |

Consequences built into v3 (3.1 for the script shift):

- arrowheads sized per `pptx_app`, and a bend never closer than 2.5 heads to
  its target;
- a slide background is always written;
- connection sites on custom shapes are written as guides (`*/ w 50000 100000`);
- script runs are written 15 % larger to offset the applications' own
  reduction, and their shift is divided by the same factor (−17 % / +28 %),
  so LibreOffice prints a subscript 0.20 × body below the line, not 0.29;
  `pptx_app: keynote` rounds type up to whole points;
- the theme's effect styles are empty and no shape references them;
- text boxes get horizontal slack;
- `pptx_connectors: freeform` writes plain paths for anyone editing in
  Keynote, whose import re-routes rotated bent connectors.

## Sub- and superscripts, measured

A `d₁` grid, base 7.5 pt, script run 1.0–1.25 × base, `baseline` −12 % to
−33 %, exported to PDF and measured with PyMuPDF:

| | printed size | drop below the base line |
|---|---|---|
| LibreOffice 26.2 | 0.585 × run size | \|baseline\| × run size |
| Keynote 14.4 | 0.67–0.69 × run size (whole points) | about 1.07 × \|baseline\| × run size |

So in LibreOffice the drop measured against the printed subscript is
\|baseline\| / 0.585, whatever the sizes. Past about 35 % (baseline below
−20 %) the subscript reads as detached. PowerPoint's and WPS's default
−25 % gives 43 %. Writing the run larger does not help, because the drop
grows with it. The engine writes −17 % on 1.15 × runs: 0.20 × body, 29 % of
the script's height. The best setting judged by eye on a real figure was
7.8 pt on 7.0 pt at −18 %, which is the same 0.20 × body.

## SVG → PDF converters

| Converter | Result | Notes |
|---|---|---|
| LibreOffice (`render.py` default) | correct glyphs, lossless pictures with the filter options | page 0.3 % larger than the SVG; `width=\linewidth` cancels it |
| rsvg-convert 2.63 (cairo/pango) | **mis-places the glyph after an italic "f"** in Times New Roman on macOS: "Deformable" prints "Deƒrmable" | the text layer still reads correctly, so only the eye catches it |
| Inkscape | not tested here | |
| cairosvg | not tested here | |

## VSDX

The VSDX writer draws arrows as polylines, not glued 1-D connectors, and has
not been checked in Visio itself. LibreOffice's VSDX import counts UTF-8 bytes
when it applies run formatting, so bold or italic after a non-ASCII glyph (→,
Greek) lands a few characters late there; the file itself marks runs
positionally, as Visio writes them. For hand-editing, prefer the PPTX.
