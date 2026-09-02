---
name: "Paper Illustration Image2"
description: "Optionally generate a non-claim-bearing visual asset when the configured image route is available."
---

# Paper Illustration Image2

Use this only after the Research Visualization Router determines that generative
imagery helps a conceptual figure and model API status reports an available
image route. It is optional; absence never blocks the paper.

Use image generation for a background, texture, or non-semantic icon. Scientific
labels, numbers, arrows, boundaries, and claim-bearing geometry must remain
editable and deterministic in the final figure.

1. Write a prompt grounded in the current paper and forbid unsupported content.
2. Generate one candidate with `python -m argus_skill.tools.image_api generate`.
3. Inspect the actual output for accidental text, watermarks, logos, misleading
   symbolism, or content not supported by the paper.
4. Place only the useful non-semantic asset into the editable figure source.

Keep the prompt only when it is needed to regenerate the included asset. Do not
create registration files or separate visual-review reports. Final acceptance
happens in the single visual pass during Review.
