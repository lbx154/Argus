---
name: "Coscientist Integration Reference"
description: "Use public Coscientist implementation material as an architecture or component reference with exact repository provenance and authorization boundaries, not as a portable autonomous laboratory or evidence of reproduced experiments."
---

## When to use

Use when a project studies or adapts a specific public planning, tool, or
integration component and can inspect its dependencies and assumptions.

## Do not use when

Do not claim the public code reproduces unavailable models, services, prompts,
hardware, instrument APIs, human supervision, or published experimental results.
Do not connect it to physical systems without explicit facility authorization.

## Required inputs

Repository/commit, license, target component, architecture/dependencies,
external services, data and credential flows, intended comparison, and
authorization/safety boundary.

## Minimum capability probe

Trace one bounded non-physical example end to end, identify every external
dependency and decision point, capture errors and outputs, and compare actual
behavior with the claimed component scope.

## Evidence and validation

Retain source/version, configuration, prompts/adapters actually used, dependency
and service versions, outputs, failures, and deviations. Domain scientific
validity requires independent checks; architecture similarity is not result
replication.

## Output contract

Return the component-level finding, reproducible inputs/outputs, missing
dependencies, security/authorization boundary, and the narrowest supported
comparison with the published system.

## Stop or replan

Stop when required services or permissions are absent, source provenance is
unclear, or validation would require unauthorized physical execution.

## Official references

- https://github.com/gomesgroup/coscientist
