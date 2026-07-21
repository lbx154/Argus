# NUMERICAL_CAPABILITY_REQUIREMENT_SPEC

The standard every **numerical** capability must follow so the Numerical Capability
gate (execute stage) can call it as a research **action**. Same 18-field schema as
`THEORY_CAPABILITY_REQUIREMENT_SPEC` (see that file for the field table);
machine-readable schema: `NUMERICAL_CAPABILITY_REQUIREMENT_SPEC.json`. The
in-source base library `argus_skill/verticals/physics/capabilities/numerical.json`
conforms to it.

## What the gate checks (philosophy)
The gate does **not** require every task to run parameter scans, neighbourhood
sweeps, or finite-size scaling. It checks the research *discipline*:

1. Did Argus **judge whether** the numerical capability is applicable?
2. If applicable, did it **execute** it with **artifact evidence**?
3. If not executed, is the **reason recorded**?
4. If a **claim depends on it** (robust/protected/phase-diagram/universal) but it
   was not done, is the **claim downgraded**?

## Gate binding
- gate_id `numerical`, execute stage.
- plan artifact `NUMERICAL_STUDY_PLAN.csv`; evidence `NUMERICAL_EVIDENCE_AUDIT.md`.
- **Claim cross-check (the distinctive part):** a `CLAIMS.csv` claim mentioning
  robust/protected/stable requires a used+evidenced robustness capability
  (**NUM-003**); a phase-diagram / phase-boundary / universal / scan claim requires
  a used+evidenced scan capability (**NUM-004**).
- Failure codes: NUM-001 (plan missing/incomplete), NUM-002 (parameter/usage
  justification or evidence missing), NUM-003 (robustness claim without robustness
  evidence), NUM-004 (phase-diagram claim without scan evidence), NUM-005 (figures
  not traceable to scripts/data), NUM-006 (unlisted numerical capability used
  without self-evaluation).

## B-side cleaning instructions
Normalise each distilled numerical capability into the 18 fields and add it to the
base library JSON or the external distilled library; the `CapabilityRegistry` loads
both compact and spec-schema records, so gates are unaffected.
