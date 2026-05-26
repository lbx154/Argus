---
name: Reviewer Engineer Handoff
description: Teach reviewer agents to translate validation and acceptance-check failures into concise, actionable prompts for smaller engineer agents.
category: review-loop
version: 1
created_at: 2026-05-25T00:00:00+00:00
---

# Reviewer-to-engineer handoff

Use this skill when a reviewer must turn validation, critique, or acceptance-check output into the next prompt for an engineer agent.

## Contract

- Treat validation output as reviewer-only evidence. The engineer should receive your distilled handoff, not a raw log dump.
- Assume the engineer may be `gpt-5.4-mini`: write short, explicit, ordered instructions with no hidden context.
- If any acceptance check fails, choose `continue` unless user input is strictly required.
- If a short deterministic check can disambiguate missing evidence, the reviewer may run it locally. Do not run long builds, model reviews, experiments, or regeneration work inside the handoff step; give the engineer the exact command and expected pass condition.
- Preserve the important facts from validation: failed command, exit code, issue codes, exact file paths, artifact paths, and validator messages.
- Group related failures by root cause and tell the engineer what to change first.
- Include the exact command that must pass before the engineer reports completion.
- Do not tell the engineer merely to "look at the validation output"; translate it into concrete work.

## `next_action` shape

Write `next_action` as a compact repair brief:

1. Goal: one sentence naming the failed acceptance condition.
2. Required fixes: ordered steps with file paths and artifact paths.
3. Verification: exact command(s) to rerun and the expected pass condition.
4. Stop rule: do not claim done until verification passes.

Keep the handoff concise. Avoid copying stack traces or long output blocks unless one or two lines are essential for diagnosis.

## Figure and paper validation handoff

When validation concerns an auto-research paper:

- For Figure 1, teaser, overall, method overview, framework, architecture, pipeline, or schematic failures, require the paper body to include the real image-generation raster output, usually a `.png`, `.jpg`, or `.jpeg` listed in `paper/figures/IMAGE2_FIGURES.json`.
- Explicitly reject self-drawn overview substitutes: matplotlib, FancyBboxPatch, TikZ/manual vector redraws, SVG/PIL/HTML canvas output, screenshots, cleaned PDFs, generic raster mockups, hand-written `codex-image2` manifests without raw generation sidecars, manual-only image reviews, or other non-image-2 replacements.
- Tell the engineer to update `paper/main.tex` and any figure-generation scripts so the body uses the image-2 artifact directly. If visual quality is the problem, instruct the engineer to write a stronger image-2 prompt and regenerate/select/review image-2 attempts, not to redraw the figure locally. Then rerun `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`.
