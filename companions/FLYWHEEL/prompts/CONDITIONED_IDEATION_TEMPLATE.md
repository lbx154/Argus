# ARGUS / FLYWHEEL · Research Protocol v2 Template

> This is the human-readable template behind Context Studio. The API compiler
> freezes concrete values and hashes the resulting condition snapshot. Do not
> launch this file with unresolved `{{...}}` placeholders.

## Mission and authority

For `{{team.name}}`, investigate the research space for
`{{venue.name}} {{venue.edition_or_cycle}} / {{venue.track}}` under the exact
conditions below. Produce a portfolio that is specific to this team. Do not
copy a bundled seed idea merely because it exists. This team's conditions,
resources, venue, source bindings and operator goal form one immutable Project
Research Protocol. A different team, resource,
data permission, date, policy, or goal is a different mission.

Oral-level quality is an aspiration, never an acceptance promise. Positive
results are not required. A supported `NO_WINNER` or negative result is valid.
No automatic submission, external contact, budget expansion, or restricted-data
access is authorized.

## Frozen condition snapshot

```json
{
  "schema_version": 3,
  "research_protocol_version": "argus.flywheel/research-protocol-v2",
  "team": {
    "name": "{{team.name}}",
    "expertise": {{team.expertise_json}},
    "methods": {{team.methods_json}},
    "data_access": {{team.data_access_json}},
    "constraints": {{team.constraints_json}},
    "goals": {{team.goals_json}},
    "policy": {{team.policy_json}},
    "metadata": {{team.metadata_json}},
    "origin": {
      "kind": "{{team.origin.kind}}",
      "source_intake_id": "{{team.origin.source_intake_id_or_empty}}",
      "operator_statement_bound": {{team.origin.operator_statement_bound}},
      "operator_statement": "{{team.origin.operator_statement}}",
      "operator_statement_sha256": "{{team.origin.operator_statement_sha256}}",
      "structured_profile_sha256": "{{team.origin.structured_profile_sha256}}",
      "extraction": {{team.origin.extraction_json}},
      "uncertainties": {{team.origin.uncertainties_json}}
    }
  },
  "venue": {{venue_snapshot_json}},
  "deadline": {{deadline_snapshot_json_or_null}},
  "resource": {{resource_snapshot_json_or_null}},
  "source_context": {
    "operator_snapshot_bound": {{source_snapshot_bound_boolean}},
    "reference_sha256": "{{source_reference_sha256_or_empty}}",
    "content_sha256": "{{source_content_sha256_or_empty}}",
    "fresh_discovery_required": {{fresh_discovery_required_boolean}}
  },
  "preflight_attestations": {{preflight_attestations_json}},
  "run": {
    "candidate_target": {{candidate_count}},
    "candidate_count": {{candidate_count}},
    "finalist_count": {{finalist_count}},
    "completion_target": "{{completion_target}}",
    "goal_contract": {{compiled_goal_contract_json}},
    "candidate_padding_forbidden": true
  }
}
```

Condition SHA-256: `{{condition_sha256}}`

If a required condition is unknown, return `NEEDS_HUMAN_DECISION`; do not fill it
with an optimistic assumption.

## Evidence intake

If `source_context.operator_snapshot_bound=true`, resolve the JSON-encoded
operator reference `{{source_snapshot_ref_json}}` and verify its bytes against
`source_context.content_sha256` before use. A mismatch terminates as `BLOCKED`.
Keep the bound packet immutable and record new retrievals as a separately
versioned freshness delta. If no packet is bound, first freeze
`SOURCE_SNAPSHOT.json` with retrieval time, exact URL/identifier, version/commit,
source kind, license/access status, and evidence extract.
Prioritize current official venue rules, official proceedings/OpenReview,
primary papers/arXiv, and upstream GitHub. Separate official facts, forecasts,
point-in-time observations, and inference. Record failed searches and nearest
novelty collisions.

## Compiled goal contract

Preserve the operator's free-form target verbatim, then compile it into:

- measurable gates with required artifact/SHA evidence;
- hard GPU/API/time/data/parallelism budget ceilings;
- explicit stop and downgrade criteria;
- valid non-positive outcomes, including `NO_WINNER` and a sealed negative result;
- a completion rule requiring either passed gates or an evidenced terminal outcome.

Oral, Best Paper, and “unprecedented” are aspirations, not measurable guarantees.
Unknown thresholds or resources require `NEEDS_HUMAN_DECISION`; never invent them.

## Independent debate

1. `DEBATER_A_BUILDER` independently aims for up to `{{candidate_count}}`
   candidates from unusual intersections of this team's capabilities, accessible
   evidence, and venue gaps. For each: mechanism, falsifier, decisive experiment,
   closest work, resources, elapsed time, licensing/ethics, and team-specific fit.
2. `DEBATER_B_BREAKER` independently reconstructs the feasible space before it
   sees A's conclusion. It then attacks collision risk, hidden data needs,
   headroom, baseline authenticity, statistics, cost/time, ethics, and venue fit;
   it may propose repairs or alternatives.
3. `ARBITER` reads frozen outputs from both tracks, preserves disagreement, and
   selects at most `{{finalist_count}}` candidates. `NO_WINNER` and ties are valid.

`{{candidate_count}}` is a discovery target, never a minimum quota. Return fewer
ideas when only fewer survive evidence and feasibility. Do not pad with cosmetic
variants, weak mechanisms, or unsupported novelty claims.

These are isolated Argus work tracks. Do not claim they are separate processes
unless runtime telemetry proves it.

## Required candidate schema

Write `CANDIDATES.json`. Every candidate contains:

```json
{
  "candidate_key": "stable key",
  "title": "mechanism-oriented working title",
  "problem_gap": "bounded gap backed by sources",
  "core_hypothesis": "falsifiable hypothesis",
  "mechanism": "why the effect should occur",
  "closest_work": ["versioned source identifiers"],
  "differentiation_claim": "claim-by-claim distinction with uncertainty",
  "public_or_authorized_data": ["version, license, access status"],
  "method": "minimal method sketch",
  "strongest_baselines": ["method@upstream-commit/config"],
  "decisive_experiments": ["cheapest decisive test"],
  "falsifier": "observation that kills or scopes the claim",
  "estimated_resources": {},
  "elapsed_time_plan": "critical path and buffer",
  "venue_fit": "current-scope evidence",
  "risks": [],
  "ethics_and_license": "constraints and gates",
  "expected_information_gain": "value including a null result",
  "terminal_recommendation": "shortlist/revise/reject/no-winner",
  "team_specific_advantage": "exact frozen capability intersection",
  "condition_fit_counterfactual": "condition change that demotes or kills this route",
  "novelty_collision_test": {
    "search_cutoff": "unambiguous point-in-time cutoff",
    "closest_source_ids": ["at least one primary source id"],
    "falsifier": "evidence that would defeat the differentiation claim"
  }
}
```

`team_specific_advantage` and `condition_fit_counterfactual` must be nonblank
strings. In the emitted candidate, `novelty_collision_test` must be an object:

```json
{
  "search_cutoff": "unambiguous point-in-time cutoff",
  "closest_source_ids": ["at least one primary source id"],
  "falsifier": "evidence that would defeat the differentiation claim"
}
```

The original operator statement remains part of the frozen condition so a
lossy extractor cannot erase a decisive constraint. Never claim a candidate is
“unprecedented” from absence of a hit; record a point-in-time, falsifiable
collision test.

Canonicalize `CANDIDATES.json` with sorted keys, compact separators and one
trailing newline, then write `CANDIDATES_MANIFEST.json` with
`schema_version=flywheel.ideation-candidates/1`, the exact condition and
objective SHA-256 values, the canonical candidates SHA-256 and candidate count.
Register both files as Argus research/delivery artifacts. Any missing artifact
or binding mismatch is quarantined.

## Staged project protocol and human checkpoints

1. `HUMAN_GATE_0_CONDITIONS`: approve exact condition/protocol hash, source rights,
   goal interpretation, budget and launch authority.
2. `RESEARCH_AND_DEBATE`: freeze Builder, Breaker and Arbiter artifacts.
3. `HUMAN_GATE_1_SELECTION`: select a finalist, request evidence, or accept
   `NO_WINNER`/negative outcome before experiments or full-paper work.
4. `EVIDENCE_AND_WRITE`: preserve all raw, failed, excluded and negative runs;
   write only claims linked to immutable evidence.
5. `INTEGRITY_CHECK_1`: citations, permissions, authentic baselines, leakage,
   statistics, claim scope, hashes and reproducibility; blockers stop promotion.
6. `INDEPENDENT_REVIEW_1`: instantiate five fresh-context, read-only reviewers.
7. `BOUNDED_REVISION`: at most two substantive revision cycles with traced diffs.
8. `INDEPENDENT_REVIEW_2`: instantiate five new fresh-context reviewers; do not
   inherit round-one scores or conclusions.
9. `FINAL_INTEGRITY_CHECK`: re-audit from primary artifacts; zero blockers required.
10. `HUMAN_GATE_2_FINAL`: approve claims, authorship, AI disclosure, venue
    compliance, final hashes and any submission action.

The five reviewer roles in each round are:

- novelty and nearest-work collision;
- methods, statistics and falsifiability;
- resource and schedule feasibility;
- venue fit and current policy;
- integrity, ethics and licensing.

Return dimension-level scores or `null` when evidence is absent. Never turn a
Viewer score into an acceptance probability, average away vetoes, or hide
disagreement. Campaign self-review is not independent review.

## Artifact and provenance contract

Version and content-address `CONDITION_SNAPSHOT`, `SOURCE_SNAPSHOT`,
`NEAREST_WORK_MATRIX`, Builder/Breaker/Arbiter outputs, `CANDIDATES`,
`CANDIDATES_MANIFEST`,
`BASELINE_PROVENANCE`, `RUN_LEDGER`, `EXCLUDED_RUNS`, `CLAIM_EVIDENCE_MATRIX`,
`STATISTICAL_AUDIT`, `VENUE_COMPLIANCE`, both review rounds, revision diffs, both
integrity reports, `REPRODUCIBILITY_MANIFEST`, final package and `PROCESS_SUMMARY`.

Each material artifact records protocol/compiler and parent version, input/prompt/
condition/source SHA, Argus SHA, model/provider, code/data revision, environment,
command, config, seed, cost, timestamp, actor and artifact SHA. Append versions;
never rewrite sealed evidence. Reproducibility means those inputs reconstruct the
reported output hash or explicitly document expected variance.

## Terminal contract

Operator target: `{{completion_target}}`

Legal states: `PORTFOLIO_READY_FOR_HUMAN_LABELING`, `NO_WINNER`,
`NOVELTY_COLLISION`, `RESOURCE_INFEASIBLE`, `INSUFFICIENT_EVIDENCE`,
`NEGATIVE_RESULT_RECORDED`, `KILLED`, `NEEDS_HUMAN_DECISION`, `BLOCKED`, and
`SUBMISSION_READY_FOR_HUMAN_REVIEW`. The last state does not mean acceptance,
Oral selection, or permission to submit.

Persist the prompt hash, condition/source snapshots, candidates, reviews, human
dimension labels, pairwise preferences and eventual outcomes. Training export
requires explicit consent, license basis and redaction confirmation; never export
secrets, identities, private paths or unlicensed text.
