---
name: Paper Illustration Image2
description: Generate and audit a research-paper illustration with the configured image-2 route. Use only after the Research Visualization Router selects generative raster rendering and model-api-status reports an available image route.
category: paper-figures
version: 2
created_at: 2026-07-19T00:00:00+00:00
---

# Paper Illustration Image2

This is the **image-2 renderer-specific procedure**, not the global research
figure policy. The research vertical's `Research Visualization Router` decides
whether image-2, deterministic SVG/HTML, a diagram tool, a data-chart tool, or
PPT Master best fits the figure.

## Preconditions

1. The figure brief explains why a generative raster is appropriate.
2. Canonical evidence, labels, and paper structure are frozen.
3. This command reports an available `image` route:

```bash
"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --model-api-status
```

If the image route is unavailable, return to the router. Do not create
`IMAGE2_OPERATOR_ACTION_REQUIRED.md`, block the whole paper solely on that API,
or label a local vector/raster as image-2.

## Ordered workflow

1. Freeze paper context:

```bash
python -m argus_skill.tools.image_tool freeze-paper-context --project-root .
```

2. Create a canonical prompt:

```bash
python -m argus_skill.tools.image_tool paper-prompt \
  --project-root . \
  --out paper/figures/<id>.prompt.txt \
  --figure-type <type> \
  --figure-title "<title>" \
  --input-label "<input>" \
  --mechanism-label "<mechanism>" \
  --verification-label "<verification>" \
  --state-label "<state>" \
  --execution-label "<execution>" \
  --output-label "<output>" \
  --evidence-label "<evidence>" \
  --caption-plan "<caption contract>" \
  --legend-plan "<legend contract>" \
  --core-step-visibility-plan "<visible mechanism>" \
  --semantic-contract "<arrow/color/icon contract>" \
  --layout-variant "<one named variant>"
```

Keep the `argus-image2-paper-prompt-v1` and
`paper-framework-figure-studio-pro-v3.1.4a` markers. Pin visible labels exactly;
never invent method names, results, or evidence.

3. Generate one candidate and wait for completion:

```bash
python -m argus_skill.tools.image_tool generate \
  --prompt-file paper/figures/<id>.prompt.txt \
  --out paper/figures/<id>.png \
  --size 1536x1024
```

4. Only after the output exists, inspect and review it:

```bash
python -m argus_skill.tools.image_tool inspect \
  --image paper/figures/<id>.png
python -m argus_skill.tools.image_tool review \
  --image paper/figures/<id>.png \
  --prompt-file paper/figures/<id>.prompt.txt \
  --out paper/figures/<id>.png.review.json
```

5. Synchronize metadata from the real files:

```bash
python -m argus_skill.tools.image_tool sync-paper-metadata \
  --project-root . \
  --image paper/figures/<id>.png \
  --prompt-file paper/figures/<id>.prompt.txt \
  --figure-id <id> \
  --figure-type <type>
```

This writes the image-2-specific `IMAGE2_FIGURES.json` and also registers the
figure in renderer-neutral `FIGURE_PROVENANCE.json`.

## Review criteria

- Logic and arrows agree with the paper and claim graph.
- Every visible label is spelled correctly and readable at final paper size.
- The composition has a clear hierarchy and appropriate aspect ratio.
- The output contains no unsupported result, hidden infrastructure detail,
  watermark, stock-logo wall, or accidental code identifier.
- Caption and figure divide explanatory work cleanly.
- The accepted raster hash matches generation, inspect, review, provenance, and
  both manifests.

Generate additional layout variants only when the current frozen context needs
them. Reuse a reviewed candidate cache when valid; do not spend image calls to
polish an unchanged context.

## Integrity rules

- Never start inspect/review before generation completes.
- Never hand-edit hashes or success-shaped review JSON.
- Never crop, resave, downsample, or overwrite an accepted raster behind its
  provenance. Regenerate or select another original candidate.
- Never treat image text or visual geometry as experimental evidence.
- Image-2 absence is a capability fact, not evidence that the paper is blocked.
