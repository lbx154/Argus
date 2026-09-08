---
name: "Using generated imagery in a paper figure"
description: "Generate a non-authoritative Method D design blueprint or non-semantic illustrative asset through an authorized configured image route."
---

# Using generated imagery in a paper figure

Use this after *Choosing how to draw a research figure* selects the default
Method D concept-figure route or a non-semantic illustrative asset, and model
API status reports an available, authorized image route. Image generation is
optional infrastructure; the paper can proceed without it using the disclosed
Method B fallback in `paper-framework-figure-studio.md`.

Use image generation for a visual design blueprint, background, texture, or
non-semantic icon. A blueprint is only a composition reference, never a source
of scientific labels, numbers, arrows, boundaries, or claim-bearing geometry.
Those must be reconstructed from authoritative sources and remain editable
and deterministic in the final figure. Do not generate quantitative result plots.

1. Inspect appropriate reference figures first for Method D. Write a minimal,
   disclosure-safe prompt grounded in the current paper and forbid unsupported
   content. Do not send private manuscripts, code, data, or credentials without
   authorization; keep secrets out of prompts and project files.
2. Generate one candidate with `python -m argus_skill.tools.image_api generate`.
3. Inspect the actual output for accidental text, watermarks, logos, misleading
   symbolism, or content not supported by the paper.
4. For Method D, retain the actual image and prompt with the figure source and
   reconstruct it through PPT Master as directed by the conceptual-figure skill.
   Do not embed the whole blueprint as a flattened substitute for native objects.
   For a decorative asset, place only its useful non-semantic portion.

Reuse a suitable existing blueprint rather than making another paid request.
For decorative assets, keep the prompt only when needed to regenerate them. Do not
create registration files or separate visual-review reports. The final visual
judgment is made in the single visual assessment during Review.
