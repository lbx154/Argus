---
name: "Chemistry Uncertainty and Applicability Domain"
description: "Quantify or bound uncertainty and determine whether a chemistry model, calculation, measurement, or literature conclusion applies to the actual chemical system."
---

## When to use

Use for quantitative prediction, parameter estimation, calibration, comparison,
extrapolation, screening, computed properties, assay interpretation, and any
claim transferred from one chemical system or protocol to another.

## Do not use when

Do not collapse uncertainty to a single model confidence score. Do not apply
generic statistical intervals when the dominant uncertainty is chemical
identity, model inadequacy, sampling bias, or protocol mismatch.

## Uncertainty inventory

Separate as applicable:

- identity and sample uncertainty;
- measurement repeatability, calibration, and systematic effects;
- finite data and label uncertainty;
- model parameter and model-form uncertainty;
- numerical, discretization, convergence, and sampling uncertainty;
- protocol, batch, operator, laboratory, and temporal variation;
- domain shift and extrapolation;
- interpretive ambiguity, such as non-unique peak assignments or mechanisms.

## Applicability-domain procedure

1. Define the target population, chemistry, conditions, and decision.
2. Identify what evidence established performance or accuracy.
3. Compare target inputs with that evidence using chemically meaningful axes:
   composition, scaffold, phase, charge/spin, functional groups, protocol,
   concentration, temperature, instrument, assay, or other domain variables.
4. Detect out-of-domain cases before ranking or optimization.
5. Use calibration, replicate analysis, sensitivity analysis, alternative
   methods, or benchmark systems appropriate to the evidence class.
6. State whether the result is interpolation, near-domain transfer, or
   extrapolation.

## Validation gates

- Intervals and error bars must name what variation they represent.
- Use group-, time-, scaffold-, composition-, batch-, or system-aware validation
  when random splitting would leak related examples.
- Compare uncertainty estimates with observed residuals or held-out controls
  when possible.
- Distinguish precision from accuracy and confidence from evidence strength.
- Do not rank candidates whose uncertainty makes their order unresolved without
  showing that ambiguity.

## Output contract

Report the estimate, uncertainty representation, dominant sources, calibration
evidence, applicability status, sensitivity to assumptions, and decision impact.
Use `unknown` when uncertainty cannot be estimated honestly.

## Stop, block, or replan conditions

Replan when the target lies outside the demonstrated domain, when uncertainty is
large enough to reverse the decision, or when the requested precision exceeds
the input or reference quality.

## Official references

- NIST uncertainty guidance: https://www.nist.gov/pml/nist-technical-note-1297
- JCGM Guides in Metrology: https://www.bipm.org/en/committees/jc/jcgm/publications
- OECD QSAR principles: https://www.oecd.org/chemicalsafety/risk-assessment/validationofqsarmodels.htm
