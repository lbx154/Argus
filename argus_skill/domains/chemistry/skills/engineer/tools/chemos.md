---
name: ChemOS Orchestration Reference
description: Evaluate ChemOS as laboratory-orchestration infrastructure only when authenticated device services, facility ownership, safety interlocks, data contracts, and rollback behavior already exist.
category: chemistry-tool-lab-orchestration
version: 2
---

## When to use

Use for architecture review or integration planning in an operator-owned,
authorized laboratory environment with existing device APIs and safety systems.

## Do not use when

Do not treat orchestration software as an instrument driver, safety controller,
authorization system, scientific workflow, or evidence that a physical
experiment occurred. Do not create simulated device success while claiming
physical execution.

## Required inputs

Exact ChemOS version/source, device/service inventory, authenticated interfaces,
command/data schemas, ownership and authorization, interlocks, safe states,
timeouts/retries, audit/event storage, recovery procedures, and a bounded
scientific workflow.

## Minimum capability probe

Use a facility-approved simulator or nonhazardous authorized endpoint to verify
one command/result lifecycle, identity, units, timeout, duplicate-command
handling, abort, safe state, and provenance. Do not bypass instrument controls.

## Evidence and validation

Retain orchestration configuration, service versions, command/result IDs,
timestamps, raw device outputs, errors, authorization reference, abort events,
and scientific validation. Separate orchestration success from instrument and
chemical correctness.

## Output contract

Return the supported orchestration boundary, device contracts, failure and
recovery behavior, safety ownership, provenance, and unresolved integration risks.

## Stop or replan

Stop when device ownership, authentication, authorization, interlocks, safe
state, or recovery behavior is absent or cannot be tested safely.

## Official references

- https://chemos.org/
- https://github.com/aspuru-guzik-group/ChemOS
