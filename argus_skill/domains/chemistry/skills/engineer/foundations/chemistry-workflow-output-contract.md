---
name: "Chemistry Workflow and Output Contract"
description: "Design a bounded chemistry workflow with explicit inputs, decisions, capability probes, evidence, validation, output fields, and stop conditions; use when no narrower domain playbook fully specifies the task."
---

## When to use

Use to frame a chemistry mission before execution or to fill gaps between
matched domain Skills. Prefer a narrower domain workflow when one exists.

## Do not use when

Do not turn this template into process paperwork. Do not create files solely to
mirror headings, and do not override project-native formats or the active
research vertical's stage lifecycle.

## Scientific question

Define the chemical system, target observable or decision, evidence level,
population or conditions, success criterion, and claim ceiling. Separate the
research objective from the next bounded action.

## Required inputs

List source data, entity/sample identity, units and conditions, available tools,
compute, licensed access, physical permissions, budget, baseline, and decision
constraints. Mark unknowns that can change method selection.

## Decision procedure

1. Resolve identity and quantitative conventions.
2. Choose the narrowest evidence-producing workflow.
3. Select capabilities from scientific requirements, not software familiarity.
4. Run a minimum capability probe.
5. Execute with primary outputs and provenance retained.
6. Apply domain validation and independent checks.
7. Compare with a strong baseline under the same budget when making a method or
   optimization claim.
8. State uncertainty, applicability, negative results, and evidence ceiling.

## Tool-selection ladder

Prefer, in order: project-native validated code or data; an established
domain-specific tool; a transparent implementation with reference checks; an
explicitly labeled manual or language-model inference only when no deterministic
capability exists. Never ask the model to imitate a chemistry engine that is
available.

## Minimum capability probe

Use one small input representative of the hard scientific feature: identity,
format, charge/spin, periodicity, disorder, protocol, instrument mode, sequence,
or other. Verify parsing, units, one expected invariant, primary output access,
and failure behavior before scaling.

## Evidence to retain

Keep original inputs, prepared inputs, transformation mapping, configuration,
versions, primary outputs, warnings, validation results, negative results, and
the claim-to-evidence mapping. Use the project's existing artifact conventions.

## Output contract

Return:

- scientific question and bounded action;
- inputs, assumptions, and unresolved identity;
- method and why it is fit for purpose;
- evidence class and retained artifacts;
- validation and controls;
- result with units, conditions, uncertainty, and applicability;
- failures and negative observations;
- maximum defensible claim;
- next decision or blocker.

## Stop, block, or replan conditions

Block when identity, inputs, capability, evidence, authorization, or validation
cannot support the requested claim. Replan instead of substituting toy data,
unvalidated proxies, or a weaker experiment while keeping the original claim.

## Official references

- IUPAC Gold Book: https://goldbook.iupac.org/
- FAIR Principles: https://www.go-fair.org/fair-principles/
- NIST Research Data Framework: https://www.nist.gov/programs-projects/research-data-framework-rdaf
