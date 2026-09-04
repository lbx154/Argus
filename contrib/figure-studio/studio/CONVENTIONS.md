# Argus Figure Studio v2 — shared conventions (read before editing anything in studio/)

The visual tokens below are aligned to the authoritative Argus
`figure_tool.py` template `argus-image2-paper-prompt-v1`.

Goal: one integrated, automated pipeline for CCF-A paper figures that combines the
Argus drawing skills that today live in separate places:

| Argus skill / asset | What we take from it |
|---|---|
| Research Visualization Router | route by semantics: data chart → Matplotlib/SciencePlots; conceptual/method/architecture → native editable PPTX via PPT Master |
| Paper Framework Figure Studio | one-sentence takeaway → exact modules/labels/connections → editable source + vector export |
| **"Figma-style" design system** (old `figure_tool.py` PAPER_FIGURE_PROMPT_TEMPLATE, removed from current Argus) | rounded cards rx 10–16, dark-gray strokes `#1f2933`, soft pastel semantic fills, compact density, numbered step badges, phase containers, chips, section tabs, no shadows/gradients/glassmorphism, 20 layout variants |
| PPT Master (installed, `~/.argus-skill/tools/ppt-master/skills/ppt-master`) | SVG authoring contract, `svg_quality_checker.py`, `svg_to_pptx.py` (native DrawingML), `pptx_to_svg.py` round-trip, `templates/icons/tabler-outline/*.svg`, `templates/charts/*.svg` as layout references, visual style `soft-rounded` |
| FigureSpec | deterministic JSON → SVG, machine-checkable geometry |
| Paper Chart Styling (`figure_spec_scripts/paper_chart_style.py`) | `set_pub_style / figure_size / highlight_ours` for the data figure |
| B-group `optimized/paper_figure_renderer.py` + `figure_quality_gate.py` | orthogonal routing, boundary ports, label pads, deterministic gate |

## Interpreter
Always `/data/v-boxiuli/argus_test_env/bin/python` (has python-pptx, Pillow, matplotlib, scienceplots, cairosvg, lxml).
PPT Master scripts: `PM=/home/v-boxiuli/.argus-skill/tools/ppt-master/skills/ppt-master`.

## Canvas and publication scale (hard rule)
- Default canvas `viewBox="0 0 1280 720"` (PPT Master `ppt169`) — the only canvas `svg_to_pptx.py -f ppt169` accepts. A flat figure root declares `data-pptx-page-role="content"`; its first visible child is the full-canvas warm-white background `<rect id="background" data-pptx-role="background" fill="#FBFAF7"/>`.
- Every contract declares `final_width_mm` (178 = double-column `figure*`, 84 = single column). Physical type is `font_px × final_width_mm / 1280 / 0.3528` pt. **Minimum 8 pt for every visible role**; at 178 mm the integer floor is 21 px (8.278 pt). The strict hierarchy is 21 px chips/labels/badges/legends/footnotes/sublabels, 24 px card labels, 27 px section/group labels, and 30 px optional page titles. Never shrink any emitted text below the floor to make geometry fit.
- Background token `#fbfaf7` always; edge-label masks, group-label masks, and icon slots use the same warm white so no white patch appears on the canvas. True white is reserved for badge-number text.

## Figma-style tokens (`studio/figma_tokens.py` owns them; nobody else hard-codes colours)
- Stroke `#1f2933` (cards 2 px, connectors 2 px), text `#111827`, secondary text `#4b5563`, group border `#9ca3af` dashed.
- Semantic pastel fills: `input/data #ffe2d1`, `process/compute #fff2bd`, `memory/storage #dcecff`, `agent/model #e2f7df`, `output/eval #eadfff`, `benchmark/metric #fff1c9`, `neutral #f3f4f6`. Highlight ("ours"/optimal path) `#d55e00` 3 px. Colour-blind-safe check: shapes/line styles must also encode meaning, not colour alone.
- Cards `rx=12`; card padding, pill padding/height, badge diameter, and badge gap are derived from their active type role in `figma_tokens.py`, with horizontal card padding never below 12 px. Pills/chips use full radius; badges use white bold minimum-scale numbers.
- Fonts: `font-family="Helvetica, Arial, 'Liberation Sans', sans-serif"`; weights 400/600/700 only.

## Layer corridors and stores
- Layer gaps are derived from corridor contents, never treated as a cosmetic fixed gap: source-to-bus clearance is at least 16 px and every marker-ended final segment is at least 28 px. Group padding, the group-label chip plus its 10 px routing obstacle, and centred edge-label pills are added to that core reservation.
- When the canvas is exhausted, layered layout compacts in this order: horizontal card padding (never below 12 px), then compact title wrapping (LR layouts only), then discretionary natural gap, then node-title type; type stops at the 21 px publication floor and a still-unfittable layout fails. Text widths are estimated from Helvetica AFM advances with a separate bold table (`figma_tokens.text_width_px`); the estimate must stay within about 3% of the rendered TeX Gyre Heros width so cards and pills are sized from real glyph metrics. Arrow and group-label clearances do not shrink. A label never changes its edge route: if a measured pill cannot fit inline, place it perpendicular to a straight segment with a 6 px, 1.5 px-wide `GROUP_BORDER` leader tick.
- A `store` is a standard-height rounded card with a second same-size card offset 4 px down/right behind it. The node's `data-pptx-bounds` encloses both cards; stores do not use interior divider lines.

## PPT Master SVG contract (must pass `$PM/scripts/svg_quality_checker.py <svg> --format ppt169` with 0 errors)
- No `<style>`, `class`, `mask`, `textPath`, `foreignObject`, `<script>`, `@font-face`, animations, HTML entities (`&rarr;` etc. — write `→` raw; escape `& < >` as XML entities).
- Inline presentation attributes only: fill, stroke, stroke-width, stroke-dasharray, stroke-linecap/linejoin, fill-opacity, stroke-opacity, opacity, font-family, font-size, font-weight, text-anchor, letter-spacing. `text-anchor` never on `<tspan>`.
- Arrowheads: `<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><polygon points="0,0 10,5 0,10" fill="#1f2933"/></marker></defs>` (3-vertex closed polygon; one marker per colour). Use `marker-end` on `<path>`/`<line>` only.
- Icons: copy the tabler-outline paths into `<symbol id="ic-<name>" viewBox="0 0 24 24" fill="none" stroke="#1f2933" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">…</symbol>` in `<defs>`, then `<use href="#ic-<name>" x y width height/>` (static same-document `<use>` is supported; `currentColor` is not — set explicit stroke). Use a moderate set only where an icon adds semantic information; repeated peer nodes do not each receive the same decorative icon, and figures never become logo walls.
- Flat pages use 3–8 descriptive top-level logical groups (`groups`, `edges`, `nodes`, `edge-labels`, `legend`, `footnote`, etc.). Every visible direct root `<g id>` carries positive `data-pptx-bounds="x y w h"`, computed to hundredth-pixel precision from the union of its visible geometry, strokes, markers, and PPT Master text-frame estimates. Nested implementation groups need no root-module bounds. The direct full-canvas background is the allowed static root primitive; no Slide-local content primitive remains ungrouped.
- `pptmaster_bridge.py` projects `design_spec.md` and `spec_lock.md` independently for every figure from its completed rendered SVG. The lock lists every emitted HEX paint, every emitted family/size, and the exact `tabler-outline/<name>` inventory with `stroke_width: 2`; do not maintain a generic superset palette that can drift from the page.
- Each logical module is `<g id="<node-id>" data-pptx-bounds="x y w h" data-node-id="<id>" data-figure-role="node">`. Groups/phases: `data-figure-role="group"`. Edge paths: `<path id="e-<id>" data-edge-id data-edge-from data-edge-to data-figure-role="edge">`. Edge labels: `<rect data-label-background="true" data-edge-id=...>` + `<text data-figure-role="edge-label" data-edge-id=...>`. Titles/section labels: `data-figure-role="title|section-label|group-label|sublabel|badge"`. These attributes are what the gate reads; keep them stable.
- Prefer `<polygon>`/`<path>` with explicit `M/L` commands; no `transform` unless unavoidable (rotated axis label only).
- Contract-mode gate error `label_covers_arrowhead`: no text box or pill background may intersect the final 20 px marker footprint expanded by 4 px. Rationale: marker geometry remains visible and separable from labels in print.
- Contract-mode gate error `edge_reenters_endpoint_node`: after its first segment an edge may not enter its source interior, and before its last segment it may not enter its target interior (node boxes are inset 2 px for the test). Rationale: endpoint loops falsely imply repeated processing and obscure routing direction.
- Contract-mode gate error `arrowheads_overlap`: marker footprints may not intersect unless their terminal points are identical (an intentional shared bus). Rationale: separate messages must retain distinct arrowheads rather than merging into an ambiguous blob.
- Other contract-mode gate errors include `edge_crosses_group_label` when an edge enters a group-label chip beyond the 1 px tolerance, and `arrowhead_clipped` when a marker-ended path's last segment is shorter than 20 px. Thresholds remain 12 px minimum SVG font, 7 pt physical minimum, 8 pt preferred, and 1 px overlap tolerance.

## Build layout for one figure (what `build_figure.py` produces)
```
studio/out/<figure_id>/
  contract.json            # copy of input
  svg_output/P01.svg       # PPT Master project source (native)
  <figure_id>.svg          # same SVG, final name
  <figure_id>.pptx         # svg_to_pptx.py output, flat structure
  <figure_id>.png          # cairosvg, width 2560
  <figure_id>.pdf          # cairosvg -f pdf
  roundtrip/               # pptx_to_svg.py output for verification
  quality/pptmaster_check.json, gate.json, build_receipt.json
```
Also copy final `<figure_id>.svg/.png/.pptx` to `studio/<figure_id>.*` for the A/B/C comparison.

## Scenarios (same 5 as A/B; contracts live in studio/contracts/)
1 `scenario_1_marl_architecture` (AAAI) · 2 `scenario_2_attention_flow` (NeurIPS) · 3 `scenario_3_federated_protocol` (ICML, sequence diagram) · 4 `scenario_4_nas_search_space` (CVPR, 4 layers × 5 candidate ops, highlighted optimal path) · 5 `scenario_5_ablation_results` (AAAI, data chart — Matplotlib route, not PPTX). Source of truth for labels: `/data/v-boxiuli/argus_figure_test/scenarios/test_scenarios.json`. Labels must match exactly.
