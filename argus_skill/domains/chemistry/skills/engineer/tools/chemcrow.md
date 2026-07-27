---
name: ChemCrow Integration Reference
description: Evaluate or reuse documented public ChemCrow tool-integration patterns as architecture references under version, dependency, service, safety, and evidence controls; not as a drop-in reproduction of published results.
category: chemistry-tool-agent-reference
version: 2
---

## When to use

Use when the project explicitly evaluates ChemCrow code or adapts a narrow,
inspectable public integration pattern to an authorized chemistry workflow.

## Do not use when

Do not assume a package or repository reproduces a paper's exact prompts,
services, datasets, models, credentials, or results. Do not enable purchasing,
laboratory action, or unsafe tool calls from a public example.

## Required inputs

Repository/release/commit, license, intended component, dependency and service
inventory, model/provider configuration, data flow, credentials boundary,
expected outputs, and safety/authorization constraints.

## Minimum capability probe

Inspect the exact source and configuration, enumerate external calls, run only a
non-sensitive authorized representative tool call, and verify logs, errors,
timeouts, and result provenance before broader reuse.

## Evidence and validation

Retain source version, configuration, dependency versions, prompts or adapters
actually used, external endpoints without secrets, requests/responses,
failures, and comparisons. Independently validate the chemistry result with the
matched domain workflow.

## Output contract

Return the reused component and modifications, capability/security boundaries,
primary outputs, validation, and differences from published claims.

## Stop or replan

Stop when provenance, license, credentials, data policy, external service, or
physical-action boundary cannot be verified.

## Official references

- https://github.com/ur-whitelab/chemcrow-public
