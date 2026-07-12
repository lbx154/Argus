---
name: Reviewer Engineer Handoff
description: Teach reviewer agents to translate validation failures into concise, actionable prompts for smaller engineer agents.
category: review-loop
version: 1
created_at: 2026-05-25T00:00:00+00:00
---

# Reviewer-to-engineer handoff

Use this skill when a reviewer must turn validation or critique into the next prompt for an engineer agent.

## Contract

- Treat validation output as reviewer-only evidence. The engineer should receive your distilled handoff, not a raw log dump.
- Do not assume the engineer shares your context: write short, explicit, ordered instructions with no hidden context.
- If verification fails, choose `continue` unless user input is strictly required.
- If a short deterministic check can disambiguate missing evidence, the reviewer may run it locally. Do not run long builds, model reviews, experiments, or regeneration work inside the handoff step; give the engineer the exact command and expected pass condition.
- Preserve the important facts from validation: failed command, exit code, issue codes, exact file paths, artifact paths, and validator messages.
- Group related failures by root cause and name the outcome that must change first.
- Preserve implementation freedom: give constraints and the evidence gap, not a
  scripted sequence, unless a deterministic failed check already implies one.
- Include an exact command only when it is the real acceptance check or the
  shortest way to disambiguate missing evidence.
- Do not tell the engineer merely to "look at the validation output"; translate it into concrete work.
- If repeated paper validators or reviews are failing, write a coherent repair brief rather than a microtask. Ask the engineer to inspect the page map, evidence sufficiency, source artifact graph, generated review freshness, and figure/table provenance, then make the smallest complete root-cause repair.

## `next_action` shape

Default to a compact outcome brief:

1. Name the failed outcome or evidence gap.
2. State hard constraints, relevant paths, and the expected proof.
3. Let the Engineer choose tools and implementation.

For a deterministic validator/test failure, a short ordered repair brief with
the exact command is appropriate. Keep either form concise; avoid copying stack
traces or long output blocks unless one or two lines are essential for diagnosis.

## Figure and paper validation handoff

When validation concerns an auto-research paper:

- For any non-data figure failure (Figure 1, teaser, overall, method overview, framework, architecture, pipeline, schematic, qualitative/example visual, or explanatory diagram), require the paper body to include the real image-generation raster output, usually a `.png`, `.jpg`, or `.jpeg` listed in `paper/figures/IMAGE2_FIGURES.json`. Data/metric/result plots are the only figures that may remain locally scripted.
- Explicitly reject self-drawn non-data substitutes: matplotlib, FancyBboxPatch, TikZ/manual vector redraws, SVG/PIL/HTML canvas output, screenshots, cleaned PDFs, generic raster mockups, hand-written `codex-image2` manifests without raw generation sidecars, manual-only image reviews, or other non-image-2 replacements.
- Tell the engineer to update `paper/main.tex` and any non-data figure-generation scripts so the body uses the image-2 artifact directly. If visual quality is the problem, instruct the engineer to create the stronger prompt with `python -m argus_skill.tools.image_tool paper-prompt ...`, retain `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a`, regenerate/select/review image-2 attempts, and run `sync-paper-metadata`, not to redraw the figure locally. Then ask for a fresh L2 reviewer round so the relevant checklist item can be re-evaluated.
