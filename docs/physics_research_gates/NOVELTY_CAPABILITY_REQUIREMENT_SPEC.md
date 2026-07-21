# NOVELTY_CAPABILITY_REQUIREMENT_SPEC

The standard every **novelty** capability must follow so the Novelty gate (review
stage) can call it as a research **action**. Same 18-field schema as
`THEORY_CAPABILITY_REQUIREMENT_SPEC`; machine-readable:
`NOVELTY_CAPABILITY_REQUIREMENT_SPEC.json`. The in-source base library
`argus_skill/verticals/physics/capabilities/novelty.json` conforms to it.

## What the gate checks (philosophy)
The gate does **not** judge whether a contribution is "truly novel". It checks the
review *discipline*:

1. Is every claim mapped to its **closest prior work**?
2. Is **already-known** separated from **what-is-new**?
3. Is **significance** (why it matters / who cares) stated?
4. Is claim **wording calibrated** to evidence strength?
5. If novelty is insufficient, are claims **downgraded** and the **paper type kept
   honest** (not an original research article)?

## Gate binding
- gate_id `novelty`, review stage; artifact `NOVELTY_CLAIM_TABLE.csv`, wording
  policy `CLAIM_WORDING_POLICY.md`.
- Failure codes: NOV-001 (claim lacks closest prior work), NOV-002 (marked new but
  already-known not separated), NOV-003 (risky/weak-evidence claim lacks
  allowed_wording), NOV-004 (all claims already known but paper_type_implication
  still original), NOV-005 (why_it_matters / who_would_care missing).
- **Feeds the Paper-Type classifier:** if novelty is insufficient, the paper type
  cannot be an original research article (see the Paper-Type gate, which also
  consumes the Literature gate result).

## B-side cleaning instructions
Normalise each distilled novelty capability into the 18 fields; the
`CapabilityRegistry` loads compact and spec-schema records alike.
