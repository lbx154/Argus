---
name: Chemistry Computational Reproducibility
description: Preserve executable inputs, environments, deterministic settings, numerical diagnostics, and primary outputs for chemistry calculations, simulations, data processing, and machine-learning workflows.
category: chemistry-foundation-reproducibility
version: 1
---

## When to use

Use whenever the result depends on code, a chemistry package, numerical
settings, a model checkpoint, a database query, stochastic sampling, or an
instrument-data processing pipeline.

## Do not use when

Do not equate rerunning the same code with independent scientific replication.
This Skill governs computational traceability; domain workflows govern whether
the method and controls answer the scientific question.

## Reproducibility record

Retain:

- original and prepared inputs, with preparation transformations;
- exact software, version, build, plugins, model/checkpoint, and data release;
- operating environment or project-native lock/container information;
- commands, configuration, random seeds, hardware details when numerically
  relevant, and parallelism settings;
- convergence thresholds, tolerances, grids, cutoffs, basis sets, force fields,
  sampling length, preprocessing, and stopping rules as applicable;
- stdout/stderr, warnings, checkpoints, primary outputs, and failure artifacts;
- code revision and the mapping from output to the claim that uses it.

## Execution procedure

1. Inspect existing project tools before adding a dependency.
2. Use a small representative capability probe.
3. Validate one known or analytically checkable case when feasible.
4. Run the intended calculation without silently changing scientific settings
   after seeing the answer.
5. Distinguish process success, numerical convergence, and scientific adequacy.
6. Preserve negative results and failed attempts that changed the workflow.
7. Re-run or independently parse a decisive result when practical.

## Validation gates

- No unrecorded manual editing of scientific inputs or outputs.
- No model artifact without source, version, checksum or stable identifier, and
  license/usage status.
- No stochastic result without seed policy and sampling diagnostics.
- No long calculation accepted solely because it reached a package's default
  termination condition.
- No plot-only evidence when machine-readable primary output exists.

## Output contract

Report the executable entrypoint, environment, inputs, configuration, primary
outputs, diagnostics, deviations from the intended method, and the exact
scientific conclusion supported.

## Stop, block, or replan conditions

Stop if the required tool, data, model, license, hardware, or authorization is
unavailable; if a capability probe fails; or if reproducibility would require
modifying protected Harness state rather than the active project environment.

## Official references

- NIST Research Data Framework: https://www.nist.gov/programs-projects/research-data-framework-rdaf
- NOMAD metadata and FAIR data: https://nomad-lab.eu/
- AiiDA provenance: https://www.aiida.net/
