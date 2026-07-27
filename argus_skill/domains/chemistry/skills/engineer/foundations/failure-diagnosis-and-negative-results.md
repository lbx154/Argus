---
name: Chemistry Failure Diagnosis and Negative Results
description: Diagnose failed chemistry calculations, experiments, analyses, searches, and models while preserving negative evidence and separating implementation defects from scientific falsification.
category: chemistry-foundation-failure
version: 1
---

## When to use

Use when a calculation does not converge, an experiment or assay fails, a
structure cannot be refined, a model underperforms, a route is infeasible, a
database search is empty, or evidence contradicts the working hypothesis.

## Do not use when

Do not force every failure into a success narrative. Do not call a hypothesis
falsified until construct, implementation, controls, sensitivity, and
applicability are adequate for that conclusion.

## Failure classes

- `input/identity`: wrong or ambiguous entity, sample, construct, structure, or
  condition.
- `capability`: unavailable tool, unsupported format, insufficient sensitivity,
  inaccessible data, compute, instrument, or authorization.
- `implementation`: parsing, code, configuration, integration, or workflow defect.
- `numerical`: convergence, sampling, precision, finite-size, optimization, or
  stability problem.
- `experimental`: preparation, calibration, control, contamination, instrument,
  batch, or protocol failure.
- `model`: approximation or learned relationship is inadequate in-domain.
- `scientific`: valid evidence does not support the hypothesis or expected effect.
- `interpretive`: multiple explanations remain observationally indistinguishable.

## Diagnostic procedure

1. Freeze the failing input, output, logs, and environment.
2. Reproduce the failure at the smallest scientifically representative scale.
3. Check identity and units before changing algorithms.
4. Test controls, known references, or orthogonal methods.
5. Change one decision-relevant factor at a time where feasible.
6. Separate remediation attempts from the original result.
7. Decide whether the result warrants repair, a different method, a narrower
   claim, a new experiment, or termination of the direction.

## Negative-result standard

A useful negative result states the tested hypothesis, domain, sensitivity,
controls, implementation adequacy, result, uncertainty, and what decisions it
changes. Preserve unsuccessful candidates and conditions to avoid repeated work
and survivorship bias. Absence of evidence is not evidence of absence unless the
workflow had adequate power and coverage.

## Output contract

Report failure class, direct evidence, ruled-out causes, unresolved causes,
scientific impact, retained artifacts, and one justified next action. Label
speculation. Do not disclose private chain-of-thought.

## Stop, block, or replan conditions

Stop repeated retries when they do not test a new cause, when the available
capability cannot reach the required sensitivity, or when safety/authorization
would be exceeded. Replan when the implementation cannot discriminate the
scientific hypotheses.

## Official references

- NIST measurement uncertainty: https://www.nist.gov/pml/nist-technical-note-1297
- Center for Open Science reporting resources: https://www.cos.io/
- OECD Good Laboratory Practice: https://www.oecd.org/chemicalsafety/testing/good-laboratory-practiceglp.htm
