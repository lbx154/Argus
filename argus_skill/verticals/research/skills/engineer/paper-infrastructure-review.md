---
name: "Paper Infrastructure Review"
description: "Read-only final check that the paper contains no private or internal implementation leakage."
---

# Paper Infrastructure Review

Use this only inside the final scientific or language Review. Read the current
manuscript and rendered paper directly.

Reject reader-facing content that exposes:

- private paths, credentials, endpoints, device assignments, or caches;
- internal role names, task IDs, stage names, validator names, or daemon details;
- development logs, debugging narration, or local-only commands that do not
  belong in a reproducible scientific description;
- contradictory descriptions of the actual model, evaluator, data, or method.

Return exact locations and replacement guidance through the current Reviewer
response. Do not edit the paper and do not create an infrastructure-review
file. The single Engineer applies the fix; the integrated verdict remains in
`paper/REVIEW.md`.
