---
name: research-platform-builder
description: Build and validate the project-specific environment, data/model bindings, evaluator, runner, telemetry, and teardown path needed for real research experiments.
---

# Research Platform Builder

Argus provides process, resource, and persistence primitives; it does not know
the domain-specific research platform in advance. The Engineer owns constructing
that platform inside the project before interpreting experimental outcomes.

1. Write `research/PLATFORM_SPEC.json` with the required artifacts and real
   command-array probes for the exact interpreter, imports, data/model bindings,
   evaluator known vector, runner smoke, telemetry, and process teardown.
2. Run `python -m argus_skill.tools.research_platform doctor --project-root .`.
3. Repair failures until `research/PLATFORM_STATUS.json` reports
   `PASS_RESEARCH_PLATFORM`.
4. Treat every failed platform probe as `platform_failure`: it updates the
   platform and does not count for or against the scientific idea.
5. Reuse the validated platform for later experiments; extend the spec when a
   new experiment genuinely needs another capability.

Keep probes faithful but small. A smoke probe must execute the real entrypoint,
not merely check that a file or package name exists.
