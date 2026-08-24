"""Conditioned ideation objectives and immutable context snapshots.

The seed catalogue is deliberately *not* treated as a universal answer.  An
ideation run freezes one team's actual capabilities, constraints and goals so
that Argus can produce a different portfolio for a different operator.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

CONDITION_SCHEMA_VERSION = 3
RESEARCH_PROTOCOL_VERSION = "argus.flywheel/research-protocol-v2"
CANDIDATE_MANIFEST_SCHEMA = "flywheel.ideation-candidates/1"
CANDIDATE_RESEARCH_SCHEMA = "flywheel.conditioned-candidate-research/1"


@dataclass(frozen=True)
class CompiledIdeationObjective:
    objective: str
    objective_sha256: str
    condition_snapshot: Mapping[str, Any]
    condition_sha256: str


@dataclass(frozen=True)
class CompiledCandidateObjective:
    objective: str
    prompt_sha256: str
    input_sha256: str
    candidate_sha256: str
    contract: Mapping[str, Any]


def compile_candidate_research_objective(
    *,
    ideation_run_id: str,
    condition_snapshot: Mapping[str, Any],
    condition_sha256: str,
    parent_objective_sha256: str,
    candidate_id: str,
    candidate_artifact_sha256: str,
    candidate: Mapping[str, Any],
    completion_target: str | None = None,
    stop_criteria: Sequence[str] | None = None,
) -> CompiledCandidateObjective:
    """Compile one selected, team-conditioned direction into an idle Argus objective."""

    for label, digest in (
        ("condition_sha256", condition_sha256),
        ("parent_objective_sha256", parent_objective_sha256),
        ("candidate_artifact_sha256", candidate_artifact_sha256),
    ):
        _required_sha256(digest, label)
    actual_condition_sha = hashlib.sha256(_canonical_json(condition_snapshot)).hexdigest()
    if actual_condition_sha != condition_sha256.lower():
        raise ValueError("condition_snapshot does not match condition_sha256")
    target = _required_text(
        completion_target
        or "Test this selected direction under the frozen conditions and return an evidence-backed result for human review; a negative result is valid.",
        "completion_target",
        4_000,
    )
    operator_stops = _clean_string_list(list(stop_criteria or []), "stop_criteria", 32)
    frozen_candidate = json.loads(json.dumps(dict(candidate), ensure_ascii=False))
    candidate_sha = hashlib.sha256(_canonical_json(frozen_candidate)).hexdigest()
    binding = {
        "schema_version": CANDIDATE_RESEARCH_SCHEMA,
        "ideation_run_id": _required_text(ideation_run_id, "ideation_run_id", 100),
        "condition_sha256": condition_sha256.lower(),
        "parent_objective_sha256": parent_objective_sha256.lower(),
        "candidate_id": _required_text(candidate_id, "candidate_id", 100),
        "candidate_artifact_sha256": candidate_artifact_sha256.lower(),
        "candidate_sha256": candidate_sha,
    }
    default_stops = [
        "nearest-work collision invalidates the bounded differentiation claim",
        "the candidate's predeclared falsifier or kill condition is observed",
        "required data, baseline, permission, compute, API, people, or elapsed time exceeds the frozen condition",
        "no authentic runnable baseline or no meaningful empirical/theoretical headroom exists",
        "integrity, ethics, privacy, safety, or license gate fails",
        "a human rejects, pauses, or declines the next gate",
    ]
    contract = {
        "binding": binding,
        "operator_completion_target": target,
        "stop_criteria": [*default_stops, *operator_stops],
        "valid_non_positive_outcomes": [
            "NEGATIVE_RESULT_RECORDED",
            "NOVELTY_COLLISION",
            "RESOURCE_INFEASIBLE",
            "INSUFFICIENT_EVIDENCE",
            "KILLED",
            "NEEDS_HUMAN_DECISION",
            "BLOCKED",
        ],
        "positive_result_required": False,
        "automatic_submission_allowed": False,
    }
    input_document = {
        "schema_version": CANDIDATE_RESEARCH_SCHEMA,
        "binding": binding,
        "condition_snapshot": condition_snapshot,
        "candidate": frozen_candidate,
        "goal_contract": contract,
    }
    input_sha = hashlib.sha256(_canonical_json(input_document)).hexdigest()
    snapshot_json = json.dumps(condition_snapshot, ensure_ascii=False, sort_keys=True, indent=2)
    candidate_json = json.dumps(frozen_candidate, ensure_ascii=False, sort_keys=True, indent=2)
    contract_json = json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)
    objective = f"""# ARGUS / FLYWHEEL · CONDITIONED CANDIDATE RESEARCH

Schema: `{CANDIDATE_RESEARCH_SCHEMA}`
Frozen input SHA-256: `{input_sha}`
Parent condition SHA-256: `{condition_sha256.lower()}`
Parent ideation objective SHA-256: `{parent_objective_sha256.lower()}`
Candidate artifact SHA-256: `{candidate_artifact_sha256.lower()}`
Candidate record SHA-256: `{candidate_sha}`

## Authority and scope

Research only this human-selected direction under the exact parent conditions
below. This is not a seed-catalogue prompt: it descends from one immutable,
team-conditioned Argus ideation artifact. Do not substitute a generic direction,
silently change the team/resource/data/deadline/venue/goal, or expand the budget.
Any such change requires a new condition version and human approval.

“Unique”, “unprecedented”, Oral, Best Paper, and acceptance are not facts or
completion criteria. Re-run nearest-claim/mechanism collision searches against
primary sources and exact upstream code revisions. Treat novelty as a dated,
falsifiable claim; preserve collisions, ambiguity, negative results, and failed
runs. Never manufacture a positive result.

## Frozen condition snapshot

```json
{snapshot_json}
```

## Frozen selected direction

```json
{candidate_json}
```

The candidate's `team_specific_advantage`, `condition_fit_counterfactual`, and
`novelty_collision_test` are hypotheses to verify, not trusted conclusions.
Start with the cheapest decisive collision/resource/falsification checks. Stop
or narrow the claim when evidence crosses a stop criterion.

## Candidate-specific completion and stop contract

```json
{contract_json}
```

## Required Argus process

1. Verify the parent hashes and freeze current official venue/source/code state.
2. Build a claim-by-claim nearest-work and baseline-authenticity matrix.
3. Have isolated Builder and Breaker tracks propose the minimum decisive plan;
   an Arbiter preserves disagreement and may return `NO_WINNER`/kill.
4. Require a human gate before paid/GPU/restricted-data experiments.
5. Preserve raw, failed, excluded, null and negative runs in an append-only ledger.
6. Map every retained claim to primary artifacts, uncertainty and a falsifier.
7. Run independent novelty, methods/statistics, resource, venue and integrity
   reviewers in two fresh-context rounds with bounded, versioned revisions.
8. End only at an evidenced non-positive state or
   `SUBMISSION_READY_FOR_HUMAN_REVIEW`; never submit automatically.

Every material artifact records this input SHA, parent condition/objective SHA,
candidate/artifact SHA, Argus/code/model/provider/data/environment versions,
commands, configs, seeds, cost, timestamps, actor and content SHA. Register final
research, integrity, reproducibility and paper artifacts through Argus's
authenticated research/delivery allowlist so FLYWHEEL can verify and ingest them.
"""
    prompt_sha = hashlib.sha256(objective.encode("utf-8")).hexdigest()
    return CompiledCandidateObjective(
        objective=objective,
        prompt_sha256=prompt_sha,
        input_sha256=input_sha,
        candidate_sha256=candidate_sha,
        contract=contract,
    )


def write_immutable_candidate_objective(
    root: Path, compiled: CompiledCandidateObjective
) -> Path:
    directory = root.resolve() / compiled.prompt_sha256
    target = directory / "CANDIDATE_OBJECTIVE.md"
    _write_once(target, compiled.objective.encode("utf-8"))
    _write_once(directory / "CANDIDATE_CONTRACT.json", _canonical_json(compiled.contract))
    return target


def compile_ideation_objective(
    *,
    team_profile: Mapping[str, Any],
    venue: Mapping[str, Any],
    deadline: Mapping[str, Any] | None,
    resource: Mapping[str, Any] | None,
    team_origin: Mapping[str, Any] | None = None,
    run_options: Mapping[str, Any] | None = None,
) -> CompiledIdeationObjective:
    """Compile an Argus objective from a frozen, team-specific condition set."""

    options = dict(run_options or {})
    candidate_count = _bounded_int(options.get("candidate_count", 10), 3, 20, "candidate_count")
    finalist_count = _bounded_int(options.get("finalist_count", 5), 1, candidate_count, "finalist_count")
    completion_target = _required_text(
        options.get("completion_target")
        or "Produce a falsifiable, evidence-backed portfolio for human selection; NO_WINNER is valid.",
        "completion_target",
        4_000,
    )
    source_snapshot_ref = _optional_text(
        options.get("source_snapshot_ref"), "source_snapshot_ref", 2_048
    )
    source_snapshot_sha256 = _optional_text(
        options.get("source_snapshot_sha256"), "source_snapshot_sha256", 64
    ).lower()
    if bool(source_snapshot_ref) != bool(source_snapshot_sha256):
        raise ValueError(
            "source_snapshot_ref and source_snapshot_sha256 must be supplied together"
        )
    if source_snapshot_sha256 and (
        len(source_snapshot_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_snapshot_sha256)
    ):
        raise ValueError("source_snapshot_sha256 must be exactly 64 hexadecimal characters")
    preflight_attestations = _boolean_mapping(
        options.get("preflight_attestations"), "preflight_attestations"
    )

    deadline_snapshot = _deadline_snapshot(deadline)
    resource_snapshot = _resource_snapshot(resource)
    team_snapshot = _team_snapshot(team_profile, team_origin)
    goal_contract = _compile_goal_contract(
        completion_target=completion_target,
        candidate_target=candidate_count,
        finalist_limit=finalist_count,
        resource_snapshot=resource_snapshot,
        deadline_snapshot=deadline_snapshot,
    )

    snapshot: dict[str, Any] = {
        "schema_version": CONDITION_SCHEMA_VERSION,
        "research_protocol_version": RESEARCH_PROTOCOL_VERSION,
        "team": team_snapshot,
        "venue": {
            "id": venue.get("id"),
            "key": _required_text(venue.get("venue_key"), "venue.key", 100),
            "name": _required_text(
                venue.get("official_name") or venue.get("display_name"), "venue.name", 300
            ),
            "category": str(venue.get("category_id") or ""),
            "metadata": _bounded_mapping(venue.get("metadata"), "venue.metadata"),
        },
        "deadline": deadline_snapshot,
        "resource": resource_snapshot,
        "source_context": {
            "operator_snapshot_bound": bool(source_snapshot_sha256),
            # The reference itself can be a private path or object locator.  It
            # is bound into the condition by digest without copying that value
            # into later training exports.
            "reference_sha256": (
                hashlib.sha256(source_snapshot_ref.encode("utf-8")).hexdigest()
                if source_snapshot_ref
                else ""
            ),
            "content_sha256": source_snapshot_sha256,
            "fresh_discovery_required": not bool(source_snapshot_sha256),
        },
        "preflight_attestations": preflight_attestations,
        "run": {
            "candidate_target": candidate_count,
            # Retained for compatibility with existing readers.  It is a
            # quality-conditioned target, never a minimum-output quota.
            "candidate_count": candidate_count,
            "finalist_count": finalist_count,
            "completion_target": completion_target,
            "goal_contract": goal_contract,
            "candidate_padding_forbidden": True,
            "oral_is_aspiration_only": True,
            "positive_result_required": False,
            "automatic_submission_allowed": False,
        },
    }
    canonical = _canonical_json(snapshot)
    condition_sha = hashlib.sha256(canonical).hexdigest()
    snapshot_json = canonical.decode("utf-8").rstrip("\n")
    if source_snapshot_ref:
        source_binding_instructions = (
            "An operator-bound source packet is available at the JSON-encoded reference "
            f"`{json.dumps(source_snapshot_ref, ensure_ascii=False)}`. Before using it, verify "
            f"its bytes have SHA-256 `{source_snapshot_sha256}`; stop with `BLOCKED` on any "
            "mismatch. Preserve that packet, then add a separately versioned freshness delta "
            "instead of silently replacing it."
        )
    else:
        source_binding_instructions = (
            "No operator-bound source packet was supplied. Before proposing candidates, create "
            "and freeze `SOURCE_SNAPSHOT.json` with retrieval times, exact identifiers, versions "
            "or commits, access/license notes, and compact evidence extracts."
        )
    goal_contract_json = json.dumps(
        goal_contract, ensure_ascii=False, sort_keys=True, indent=2
    )
    objective = f"""# ARGUS / FLYWHEEL · RESEARCH PROTOCOL v2

## Mission and authority boundary

You are running an evidence-first research ideation mission for one explicitly
described team and one venue.  This prompt, the team/resource/venue conditions,
the source bindings, and the operator goal are one immutable Project Research
Protocol.  Do not reuse a generic idea merely because it
appears in a seed catalogue.  The frozen condition snapshot below is the source
of truth for feasibility.  Another team, resource envelope, data permission,
date, or goal must be treated as a different task and may produce a completely
different portfolio.

The quality aspiration is work that could survive an oral-level review bar, not
a promise of acceptance or a request to manufacture a positive result.  A
well-supported NO_WINNER, collision, or negative finding is preferable to a
novelty claim without evidence.  Never submit, contact people, expand budget,
use restricted data, or change the frozen conditions without a human gate.

## Frozen condition snapshot

Protocol: `{RESEARCH_PROTOCOL_VERSION}`
Condition schema: {CONDITION_SCHEMA_VERSION}
Condition SHA-256: `{condition_sha}`

```json
{snapshot_json}
```

## Required source discipline

1. {source_binding_instructions}
   Prefer official venue pages, official proceedings or OpenReview records,
   primary papers/arXiv, and upstream GitHub repositories.
2. Separate official facts, point-in-time observations, forecasts, and your own
   inference.  Never silently convert a forecast deadline into an official one.
3. Search recent and prior accepted work for nearest-neighbour collisions.  For
   code claims, inspect the exact upstream commit.  Record failed searches too.
4. Treat the bundled FLYWHEEL seed ideas only as coverage probes and counterexamples.
   They are not personalized candidates and receive no novelty presumption.

## Compiled operator goal

The operator's free-form aspiration has been compiled into the following
machine-checkable contract.  A gate can pass only with cited, hashed evidence;
the budget is a hard ceiling; stop criteria override the aspiration.

```json
{goal_contract_json}
```

If a required value is unknown or unbound, return `NEEDS_HUMAN_DECISION` before
the affected stage.  Never invent a resource, deadline, permission, metric, or
success threshold to make a gate pass.

## Two-sided debate protocol

Create two isolated reasoning tracks before either sees the other's conclusion:

- `DEBATER_A_BUILDER`: aim for up to {candidate_count} defensible candidates from the
  team's unusual capability combinations, venue gaps, and accessible evidence.
  Each candidate must state the mechanism, falsifier, decisive experiment,
  closest work, estimated compute/API/data/time cost, and why this team can do it.
- `DEBATER_B_BREAKER`: independently reconstruct the feasible research space,
  then attack every Builder candidate for collision, weak headroom, hidden data
  access, unrealistic time/compute, metric gaming, missing baseline, ethics,
  licensing, or venue mismatch.  It must propose repairs and may introduce its
  own candidates.
- `ARBITER`: compare the two artifact sets only after both are frozen.  Preserve
  disagreements.  Select at most {finalist_count} finalists, allow ties, and
  output `NO_WINNER` when the evidence does not support a survivor.

The target of {candidate_count} is a discovery target, not a quota.  Never pad
the portfolio with cosmetic variants, weak candidates, or unsupported novelty
claims.  Fewer candidates, zero finalists, and `NO_WINNER` are correct outputs
when the evidence warrants them.

These are isolated Argus work tracks/artifacts; do not claim they are separate
OS processes unless the runtime telemetry proves that they are.

## Candidate contract

Write `CANDIDATES.json` as an array.  Every item must contain:

`candidate_key`, `title`, `problem_gap`, `core_hypothesis`, `mechanism`,
`closest_work` (with source ids), `differentiation_claim`, `public_or_authorized_data`,
`method`, `strongest_baselines`, `decisive_experiments`, `falsifier`,
`estimated_resources`, `elapsed_time_plan`, `venue_fit`, `risks`,
`ethics_and_license`, `expected_information_gain`, `terminal_recommendation`,
`team_specific_advantage`, `condition_fit_counterfactual`, and
`novelty_collision_test`.

`team_specific_advantage` must name the exact frozen capability/data/resource
intersection that makes the route fit this team. `condition_fit_counterfactual`
must say which materially different team condition would demote or kill it.
`novelty_collision_test` must record search cutoffs, primary source ids, nearest
claim/mechanism neighbours, unresolved uncertainty, and the observation that
would falsify the differentiation claim. Never label a candidate “unprecedented”
or “unique” merely because no collision was found; novelty remains a
point-in-time, falsifiable evidence claim.

The first two fields are nonblank strings. `novelty_collision_test` is an object
with these exact importer-required keys (additional evidence fields are allowed):

```json
{{
  "search_cutoff": "ISO date/time or another unambiguous point-in-time cutoff",
  "closest_source_ids": ["at least one nonblank primary source identifier"],
  "falsifier": "the prior claim/mechanism evidence that would defeat differentiation"
}}
```

Do not optimize a single composite score.  Produce dimension-level assessments
for novelty evidence, falsifiability, resource fit, venue fit, methodological
soundness, integrity risk, and expected information gain.  Preserve uncertainty
and reviewer disagreement so humans can label or pairwise-rank candidates later.

After freezing `CANDIDATES.json`, serialize it as UTF-8 canonical JSON with
lexicographically sorted object keys, compact separators (`,` and `:`), and one
trailing newline. Compute SHA-256 over those exact bytes. Then write
`CANDIDATES_MANIFEST.json` with exactly these binding fields:

```json
{{
  "schema_version": "{CANDIDATE_MANIFEST_SCHEMA}",
  "condition_sha256": "{condition_sha}",
  "objective_sha256": "SHA-256 of the exact FLYWHEEL IDEATION_OBJECTIVE.md bytes",
  "candidates_sha256": "SHA-256 of canonical CANDIDATES.json bytes",
  "candidate_count": "integer length of CANDIDATES.json"
}}
```

The runtime-provided objective SHA is authoritative; if it is unavailable or
does not match the exact received objective bytes, stop with `BLOCKED`. Register
both files as Argus research/delivery artifacts so the authenticated artifact
allowlist exposes them. A missing registration, schema mismatch, condition or
objective mismatch, candidate-count mismatch, or content-digest mismatch means
the pair is quarantined and must not enter FLYWHEEL.

## Project lifecycle, review panel, and human checkpoints

The Project Protocol is staged and may not silently skip a gate:

1. `HUMAN_GATE_0_CONDITIONS`: a human confirms the condition snapshot, source
   permissions, budget, goal contract, and launch authority.
2. `RESEARCH_AND_DEBATE`: Builder and Breaker freeze independent artifacts;
   Arbiter produces a traceable decision, including `NO_WINNER`.
3. `HUMAN_GATE_1_SELECTION`: a human selects a finalist, requests more evidence,
   or accepts the negative/no-winner outcome.  No full-paper campaign starts
   merely because the Arbiter selected a candidate.
4. `EVIDENCE_AND_WRITE`: execute only approved, budgeted experiments; preserve
   raw/failed/negative runs; write only claims supported by the ledger.
5. `INTEGRITY_CHECK_1`: verify citations, data rights, baseline authenticity,
   leakage, statistics, claim scope, and artifact hashes.  Any blocker stops promotion.
6. `INDEPENDENT_REVIEW_1`: instantiate five fresh-context, read-only reviewers:
   novelty/collision; methods/statistics/falsifiability; resources/schedule;
   venue/policy; integrity/ethics/licensing.
7. `BOUNDED_REVISION`: respond claim-by-claim with evidence, for at most two
   substantive revision cycles; never overwrite raw evidence or prior versions.
8. `INDEPENDENT_REVIEW_2`: instantiate five new fresh-context reviewers in the
   same roles.  They must not inherit scores or conclusions from round one.
9. `FINAL_INTEGRITY_CHECK`: re-audit from primary artifacts and require zero
   unresolved integrity blockers.
10. `HUMAN_GATE_2_FINAL`: a human confirms claims, authorship, AI disclosure,
    venue compliance, final hashes, and any submission action.

Each reviewer cites the immutable artifact version/SHA it examined and returns
`score: null` if evidence is absent.  Do not average away vetoes or disagreement,
turn reviewer scores into acceptance probability, or let campaign self-review
count as an independent review.

Operator completion target:

> {completion_target}

Legal terminal states are `PORTFOLIO_READY_FOR_HUMAN_LABELING`, `NO_WINNER`,
`NOVELTY_COLLISION`, `RESOURCE_INFEASIBLE`, `INSUFFICIENT_EVIDENCE`,
`NEGATIVE_RESULT_RECORDED`, `KILLED`, `NEEDS_HUMAN_DECISION`, `BLOCKED`, and
`SUBMISSION_READY_FOR_HUMAN_REVIEW`.  The operator target cannot override
integrity, resource, permission, or human-submission gates.  The last state is
not acceptance, Oral selection, or permission to submit.

## Artifact, reproducibility, and provenance contract

Maintain immutable, versioned, content-addressed artifacts for:
`SOURCE_SNAPSHOT`, `NEAREST_WORK_MATRIX`, Builder/Breaker/Arbiter outputs,
`CANDIDATES`, `CANDIDATES_MANIFEST`, `BASELINE_PROVENANCE`, `RUN_LEDGER`, `EXCLUDED_RUNS`,
`CLAIM_EVIDENCE_MATRIX`, `STATISTICAL_AUDIT`, `VENUE_COMPLIANCE`, both review
rounds, both integrity reports, revision diffs, final paper/package, and
`PROCESS_SUMMARY`.

Every material record must state protocol version, parent version, prompt SHA,
condition SHA, source SHA(s), Argus code SHA, model/provider identifier, code and
data revisions, configuration, seeds, command, environment, cost, timestamps,
actor (human or agent), and artifact SHA.  Append new versions; never rewrite a
sealed artifact.  A result is reproducible only when an authorized operator can
reconstruct the exact input, code, environment, command, seed, and output hash.

## Dataset trace

Persist the protocol version, frozen condition hash, source snapshot hash, prompt hash, exact
candidate JSON, all independent reviews, human dimension labels, pairwise
preferences, decisions, and eventual outcomes.  Do not mark any record eligible
for training unless explicit consent, a license basis, and redaction confirmation
are present.  Never include secrets, reviewer identities, private paths, or
unlicensed text in a training export.
"""
    objective_sha = hashlib.sha256(objective.encode("utf-8")).hexdigest()
    return CompiledIdeationObjective(
        objective=objective,
        objective_sha256=objective_sha,
        condition_snapshot=snapshot,
        condition_sha256=condition_sha,
    )


def write_immutable_objective(root: Path, compiled: CompiledIdeationObjective) -> Path:
    """Write an objective below a content-addressed FLYWHEEL-owned directory."""

    directory = root.resolve() / compiled.objective_sha256
    target = directory / "IDEATION_OBJECTIVE.md"
    _write_once(target, compiled.objective.encode("utf-8"))
    snapshot = _canonical_json(compiled.condition_snapshot)
    _write_once(directory / "CONDITION_SNAPSHOT.json", snapshot)
    return target


def _compile_goal_contract(
    *,
    completion_target: str,
    candidate_target: int,
    finalist_limit: int,
    resource_snapshot: Mapping[str, Any] | None,
    deadline_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Turn a free-form aspiration into bounded, evidence-verifiable gates.

    This deliberately does not pretend to understand an arbitrary scientific
    target semantically.  It preserves the text verbatim, wraps it in universal
    integrity/reproducibility gates, and requires a human to supply any missing
    project-specific threshold before that gate can pass.
    """

    resource_bound = resource_snapshot is not None
    deadline_bound = deadline_snapshot is not None
    return {
        "operator_aspiration": completion_target,
        "interpretation_rule": (
            "Aspiration only; it never overrides evidence, integrity, permission, "
            "budget, stop, or human-approval gates."
        ),
        "measurable_gates": [
            {
                "gate": "G0_CONDITIONS_CONFIRMED",
                "pass_when": (
                    "A named human approves this exact condition/protocol hash, "
                    "source rights, goal interpretation, and hard budget."
                ),
                "evidence": ["HUMAN_APPROVAL.json", "CONDITION_SNAPSHOT.json"],
            },
            {
                "gate": "G1_PORTFOLIO_EVIDENCED",
                "pass_when": (
                    f"Zero to {candidate_target} non-padded candidates have complete "
                    "source, mechanism, falsifier, resource, venue, and risk fields; "
                    "NO_WINNER is valid."
                ),
                "evidence": [
                    "SOURCE_SNAPSHOT.json",
                    "NEAREST_WORK_MATRIX.json",
                    "CANDIDATES.json",
                    "CANDIDATES_MANIFEST.json",
                ],
            },
            {
                "gate": "G2_ARBITRATION_COMPLETE",
                "pass_when": (
                    f"Builder and Breaker are independently frozen and the Arbiter "
                    f"selects at most {finalist_limit} finalists or records NO_WINNER."
                ),
                "evidence": [
                    "BUILDER_OUTPUT.json",
                    "BREAKER_OUTPUT.json",
                    "ARBITER_DECISION.json",
                ],
            },
            {
                "gate": "G3_CLAIM_EVIDENCE_READY",
                "pass_when": (
                    "Every retained claim maps to primary evidence; authentic baselines, "
                    "raw and failed runs, uncertainty, and reproducibility metadata exist."
                ),
                "evidence": [
                    "CLAIM_EVIDENCE_MATRIX.json",
                    "BASELINE_PROVENANCE.json",
                    "RUN_LEDGER.jsonl",
                    "REPRODUCIBILITY_MANIFEST.json",
                ],
            },
            {
                "gate": "G4_TWO_STAGE_REVIEW_PASSED",
                "pass_when": (
                    "Two independent rounds of five fresh-context reviewers are complete, "
                    "bounded revisions are traced, and no unresolved reviewer veto remains."
                ),
                "evidence": [
                    "REVIEW_ROUND_1/",
                    "REVISION_TRACE.json",
                    "REVIEW_ROUND_2/",
                ],
            },
            {
                "gate": "G5_FINAL_INTEGRITY_AND_HUMAN_RELEASE",
                "pass_when": (
                    "A from-scratch final integrity audit has zero blockers and a named "
                    "human approves the exact final artifact hashes."
                ),
                "evidence": ["FINAL_INTEGRITY_REPORT.json", "FINAL_HUMAN_APPROVAL.json"],
            },
        ],
        "budget": {
            "resource_snapshot_bound": resource_bound,
            "resource_ceiling": resource_snapshot,
            "deadline_snapshot_bound": deadline_bound,
            "time_ceiling": deadline_snapshot,
            "expansion_requires_new_version_and_human_approval": True,
            "missing_bound_action": (
                "NEEDS_HUMAN_DECISION before paid/API/GPU/data execution"
                if not resource_bound
                else "enforce frozen resource ceiling"
            ),
        },
        "stop_criteria": [
            "direct novelty collision that cannot be repaired without a new claim",
            "kill/falsification criterion is met",
            "no authentic runnable baseline or no meaningful headroom",
            "resource, deadline, data-rights, ethics, privacy, or license infeasibility",
            "budget ceiling reached or required permission/condition is unknown",
            "integrity blocker persists after bounded repair",
            "human pauses, rejects, or declines the next gate",
        ],
        "valid_non_positive_outcomes": [
            "NO_WINNER",
            "NEGATIVE_RESULT_RECORDED",
            "NOVELTY_COLLISION",
            "KILLED",
            "RESOURCE_INFEASIBLE",
            "INSUFFICIENT_EVIDENCE",
        ],
        "completion_rule": (
            "Complete only when every applicable measurable gate passes or a valid "
            "non-positive terminal outcome is sealed with evidence."
        ),
    }


def _deadline_snapshot(deadline: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if deadline is None:
        return None
    return {
        "id": deadline.get("id"),
        "conference_year": deadline.get("conference_year"),
        "deadline_date": deadline.get("deadline_date"),
        "timezone": deadline.get("timezone"),
        "round_note": deadline.get("round_note"),
        "evidence_status": deadline.get("evidence_status"),
        "forecast_window_start": deadline.get("forecast_window_start"),
        "forecast_window_end": deadline.get("forecast_window_end"),
        "confidence": deadline.get("confidence"),
        "requires_confirmation": bool(deadline.get("requires_confirmation")),
        "source_url": deadline.get("source_url"),
        "metadata": _bounded_mapping(deadline.get("metadata"), "deadline.metadata"),
    }


def _resource_snapshot(resource: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if resource is None:
        return None
    return {
        "id": resource.get("id"),
        "name": resource.get("name"),
        "resource_type": resource.get("resource_type"),
        "capacity": _bounded_mapping(resource.get("capacity"), "resource.capacity"),
        "availability_state": resource.get("availability_state"),
        "enabled": bool(resource.get("enabled")),
        "metadata": _bounded_mapping(resource.get("metadata"), "resource.metadata"),
    }


def _team_snapshot(
    team_profile: Mapping[str, Any], team_origin: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Freeze both the confirmed structure and the operator's original words.

    The original one-sentence intake is part of the scientific condition, not
    disposable UI copy.  Keeping it beside the normalized fields prevents a
    lossy extractor from making two materially different teams look identical.
    """

    semantic_profile = {
        "name": _required_text(team_profile.get("name"), "team.name", 200),
        "expertise": _clean_string_list(team_profile.get("expertise"), "team.expertise", 64),
        "methods": _clean_string_list(team_profile.get("methods"), "team.methods", 64),
        "data_access": _clean_string_list(team_profile.get("data_access"), "team.data_access", 64),
        "constraints": _bounded_mapping(team_profile.get("constraints"), "team.constraints"),
        "goals": _bounded_mapping(team_profile.get("goals"), "team.goals"),
        "policy": _bounded_mapping(team_profile.get("policy"), "team.policy"),
        "metadata": _bounded_mapping(team_profile.get("metadata"), "team.metadata"),
    }
    origin_input = dict(team_origin or {})
    statement = _optional_text(
        origin_input.get("operator_statement"), "team.origin.operator_statement", 64 * 1024
    )
    source_intake_id = _optional_text(
        origin_input.get("source_intake_id"), "team.origin.source_intake_id", 100
    )
    origin_kind = _optional_text(origin_input.get("kind"), "team.origin.kind", 100) or (
        "confirmed_operator_intake" if statement else "structured_profile"
    )
    structured_sha = hashlib.sha256(_canonical_json(semantic_profile)).hexdigest()
    origin = {
        "kind": origin_kind,
        "source_intake_id": source_intake_id,
        "operator_statement_bound": bool(statement),
        "operator_statement": statement,
        "operator_statement_sha256": (
            hashlib.sha256(statement.encode("utf-8")).hexdigest() if statement else ""
        ),
        "structured_profile_sha256": structured_sha,
        "extraction": _bounded_mapping(origin_input.get("extraction"), "team.origin.extraction"),
        "uncertainties": _clean_string_list(
            origin_input.get("uncertainties") or [], "team.origin.uncertainties", 64
        ),
    }
    return {
        "profile_id": str(team_profile.get("id") or ""),
        **semantic_profile,
        "origin": origin,
    }


def _clean_string_list(value: Any, field: str, maximum: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{field} has more than {maximum} entries")
    cleaned: list[str] = []
    for item in value:
        text = _required_text(item, field, 1_000)
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def _bounded_mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result = json.loads(json.dumps(dict(value), ensure_ascii=False))
    encoded = _canonical_json(result)
    if len(encoded) > 64 * 1024:
        raise ValueError(f"{field} exceeds 64 KiB")
    return result


def _boolean_mapping(value: Any, field: str) -> dict[str, bool]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if len(value) > 64:
        raise ValueError(f"{field} has more than 64 entries")
    result: dict[str, bool] = {}
    for raw_key, raw_value in value.items():
        key = _required_text(raw_key, field, 200)
        if not isinstance(raw_value, bool):
            raise ValueError(f"{field}.{key} must be a boolean")
        result[key] = raw_value
    return dict(sorted(result.items()))


def _bounded_int(value: Any, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _required_sha256(value: Any, field: str) -> str:
    text = _required_text(value, field, 64).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be exactly 64 hexadecimal characters")
    return text


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be blank")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _optional_text(value: Any, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError("content-addressed objective collision")
        return
    # Keep the temporary basename short enough for Windows' legacy MAX_PATH;
    # the parent already contains a 64-character content digest.
    temporary = path.parent / f".tmp-{uuid.uuid4().hex[:8]}"
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
