---
name: "Keeping private implementation details out of the paper"
description: "Read the final paper without editing it and check that it exposes no private or internal implementation details."
---

# Keeping private implementation details out of the paper

Use this only inside the final scientific or language Review. Read the current
manuscript and rendered paper directly.

Find that the paper does not hold yet if its reader-facing content exposes:

- private paths, credentials, endpoints, device assignments, or caches;
- internal role names, task IDs, stage names, validator names, or daemon details;
- development logs, debugging narration, or local-only commands that do not
  belong in a reproducible scientific description;
- contradictory descriptions of the actual model, evaluator, data, or method.

Return exact locations and replacement guidance through the current Reviewer
response. Do not edit the paper and do not create an infrastructure-review
file. The single Engineer applies the fix; the integrated judgment remains in
`paper/REVIEW.md`.
