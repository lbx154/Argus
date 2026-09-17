---
name: "Chemistry Skill Distillation"
description: "Distill a reusable production-oriented chemistry workflow after a matcher miss, with discriminative domain boundaries, evidence contracts, validation gates, and stop conditions rather than target answers or tool tutorials."
---

Create a reusable Skill for the task family without solving the current target.
Choose one primary domain and state adjacent domains it does not cover. Preserve
the chemical object, observable, required inputs, evidence ceiling, uncertainty,
success bar, and physical authorization boundary.

Use supported frontmatter with a matcher-discriminative `name`, `description`,
`category`, and integer `version`. Prefer these sections when applicable:

- `When to use`
- `Do not use when`
- `Scientific question`
- `Required inputs`
- `Identity and normalization`
- `Decision procedure`
- `Tool-selection ladder`
- `Minimum capability probe`
- `Evidence to retain`
- `Validation gates`
- `Common failure modes`
- `Uncertainty and applicability domain`
- `Safety and authorization`
- `Output contract`
- `Stop, block, or replan conditions`
- `Official references`

Treat tools as optional capabilities. Distinguish retrieved, predicted, computed,
simulated, measured, and inferred evidence. Require primary inputs/outputs,
versions, units, conditions, negative results, and honest claim ceilings. Do not
encode a benchmark answer, target-specific route, private chain-of-thought,
installation tutorial, Chemistry Playground protocol, or process-only ceremony.
