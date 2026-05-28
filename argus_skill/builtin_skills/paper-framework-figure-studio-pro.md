---
name: Paper Framework Figure Studio Pro
description: "Argus-adapted workflow for paper-grounded method, architecture, pipeline, and agent-workflow figures. Use when a paper needs a non-data conceptual figure, candidate figure exploration, image-caption co-design, core-mechanism visibility, semantic arrow/color/icon review, or final joint audit. All rendered target-paper figures must use image-2 through argus_skill.tools.image_tool."
category: paper-figures
version: "3.1.4a-argus"
scientist_model: gpt-5.4
created_at: "2026-05-28"
source: "paper-framework-figure-studio-pro-v3.1.4a"
---

# Paper Framework Figure Studio Pro

This is the Argus-native adaptation of `paper-framework-figure-studio-pro-v3.1.4a`.
It keeps the useful drawing discipline while removing the original human-turn
alternation constraint, because Argus missions are autonomous reviewer-gated
workflows.

## Required Route

Every target-paper conceptual figure must be a generated image-2 raster:

```bash
python -m argus_skill.tools.image_tool paper-prompt --out paper/figures/<id>.prompt.txt ...
python -m argus_skill.tools.image_tool generate --prompt-file paper/figures/<id>.prompt.txt --out paper/figures/<id>.png --size 1536x1024 --force
python -m argus_skill.tools.image_tool inspect --image paper/figures/<id>.png
python -m argus_skill.tools.image_tool review --image paper/figures/<id>.png --prompt-file paper/figures/<id>.prompt.txt --out paper/figures/<id>.png.review.json
python -m argus_skill.tools.image_tool sync-paper-metadata --project-root . --image paper/figures/<id>.png --prompt-file paper/figures/<id>.prompt.txt --figure-id <id> --figure-type method
python -m argus_skill.skills.pipeline_contracts validate-image2-figures --project-root .
```

Do not hand-write image prompts from scratch. Do not delete these prompt markers:
`argus-image2-paper-prompt-v1` and
`paper-framework-figure-studio-pro-v3.1.4a`.

## Workflow

Use the studio stages as an internal checklist:

- `S0-PAPER-FOUNDATION`: extract method facts, module names, arrow relations, core mechanism substeps, evidence anchors, and what must not appear in the figure.
- `S1-FIGURE-STRATEGY`: decide figure role, reader path, layout grammar, visual density, and which facts belong in pixels vs caption/legend.
- `S2-SKETCH-EXPLORE`: optionally generate broad image-2 candidate rasters when the figure direction is unclear.
- `S3-DIRECTION-SELECT`: pick a direction based on paper fidelity, reader path, and core mechanism visibility.
- `S4-CANDIDATE-BRIEF`: write candidate contracts: title, caption plan, legend plan, body reference plan, core-step visibility plan, symbol/formula necessity, and arrow/color/icon semantic contract.
- `S5-CANDIDATE-IMAGE`: generate separate formal image-2 raster candidates from `paper-prompt`; never make a contact sheet or local redraw.
- `S6-FINAL-SELECT`: choose one generated raster and draft final title, caption, legend, and body-reference text.
- `S7-FINAL-JOINT-AUDIT`: run one bounded reviewer audit of the selected image plus caption/legend/body-reference bundle.

## Figure Rules

- Data/metric/result plots may be scripted from raw data. Method overviews,
  architecture/system diagrams, workflow schematics, qualitative/example
  visuals, and teasers must use image-2.
- The figure should show the main reader path and core mechanism anchors; the
  caption/legend should carry definitions, caveats, detailed numbers, and
  nonessential symbol explanations.
- A core contribution module cannot be an empty generic box. Show its internal
  mechanism through nested cards, a connected inset, a compact loop, or a small
  mechanism panel.
- Arrow direction, color, line style, icon family, and symbol use need a semantic
  contract before generation and a reviewer audit after generation.
- If prompt/provenance/manifest hashes drift, run `sync-paper-metadata` from the
  real generated files or regenerate. Do not patch JSON hashes by hand.
