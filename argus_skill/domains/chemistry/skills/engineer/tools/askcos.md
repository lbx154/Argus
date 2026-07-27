---
name: ASKCOS Retrosynthesis
description: Use an operator-authorized ASKCOS deployment for retrosynthesis or reaction prediction with endpoint, model, database, access, budget, and result provenance; never assume hosted access or experimental validity.
category: chemistry-tool-retrosynthesis
version: 2
---

## When to use

Use only when the project has documented authorized access to a specific ASKCOS
deployment whose capabilities and versions can be inspected.

## Do not use when

Do not assume a public hosted service, bypass authentication, expose private
structures to an unapproved endpoint, or infer that server success proves route
feasibility, yield, or reagent availability.

## Required inputs

Deployment owner/authorization, endpoint and API version, task type, target or
reaction identity, model/database/stock versions where exposed, search budget,
privacy constraints, and result-retention policy.

## Minimum capability probe

Use a non-sensitive representative input, verify authentication and endpoint
semantics, capture model/version metadata, inspect one result, and confirm error,
timeout, rate-limit, and unavailable-service behavior.

## Evidence and validation

Retain approved endpoint identity without credentials, request parameters,
response, timestamps, model/database metadata, budget, errors, and transformations.
Apply organic synthesis validation to routes and reaction predictions.

## Output contract

Return retrieved model outputs with provenance, deployment limits, failure
states, privacy status, and `predicted/generated-not-validated` wording.

## Stop or replan

Stop when authorization, privacy terms, model/version metadata, or inspectable
results are unavailable. Never work around access controls.

## Official references

- https://askcos.mit.edu/
- https://github.com/ASKCOS/ASKCOS
