# THEORY_CAPABILITY_REQUIREMENT_SPEC

The standard every **theory capability** must follow so the Theory Capability gate
(model stage) can call it as a research **action**, not a knowledge point. The
B-side cleans/curates the theory capability library to this schema. The in-source
base library `argus_skill/verticals/physics/capabilities/theory.json` conforms to
this spec as the reference example. Machine-readable schema:
`THEORY_CAPABILITY_REQUIREMENT_SPEC.json`.

## What the gate checks (philosophy)
The gate does **not** check for a fixed theoretical structure (phase diagram,
invariant, effective Hamiltonian, …). It checks the research *discipline*:

1. Did Argus **judge whether** the capability is applicable?
2. If applicable, did it **execute** the capability?
3. If executed, is there **artifact evidence**?
4. If not executed, is the **reason recorded**?
5. If a **claim depends on it** but it was not done, is the **claim downgraded**?

## Required fields (18) per theory capability
| # | field | meaning |
|---|---|---|
| 1 | `capability_id` | stable unique id |
| 2 | `capability_name` | short name of the research action |
| 3 | `domain` | domain / subdomain (`*` = generic) |
| 4 | `applicability_question` | when does it apply? |
| 5 | `non_applicability_condition` | when does it NOT apply? (legitimises "not used") |
| 6 | `usage_question` | did Argus use it? |
| 7 | `evidence_question` | where is the evidence file? |
| 8 | `minimum_artifact` | minimum acceptable product |
| 9 | `recommended_artifacts` | high-quality products |
| 10 | `basic_standard` | basic pass standard |
| 11 | `advanced_standard` | stronger standard |
| 12 | `publishable_standard` | near paper-level standard |
| 13 | `failure_codes` | how it fails when missing / done poorly |
| 14 | `repair_actions` | concrete per-failure repair actions |
| 15 | `claim_downgrade_if_missing` | how a dependent claim is downgraded if not done |
| 16 | `comparison_to_prior_work_requirement` | how to compare to the closest prior theoretical treatment |
| 17 | `example_good_use` | concrete good-use example |
| 18 | `example_bad_use` | concrete bad / superficial example |

## Gate binding
The capability's `applicability_question` / `usage_question` / `evidence_question`
/ `claim_downgrade_if_missing` map to the `THEORY_OPPORTUNITY_AUDIT.csv` columns
`is_applicable` / `used_by_argus` / `evidence_file` / `claim_downgrade_if_missing`
that `gates/theory.py` verifies (gate_id `theory`, model stage; domain from
`DOMAIN_CLASSIFICATION.json`).

## B-side cleaning instructions
When distilling more theory capabilities from the literature corpus, normalise
each into a record with all 18 fields above and add it to the base library JSON or
to the external distilled library. `CapabilityRegistry` loads both compact and
spec-schema records (field-name fallbacks), so existing gates are unaffected.
