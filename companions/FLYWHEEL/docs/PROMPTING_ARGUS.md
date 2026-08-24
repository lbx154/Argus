# ARGUS / FLYWHEEL · How to feed Argus a Research Protocol v2

The most reliable Argus input is a frozen execution contract, not “write an innovative
paper about X.” There is no single best Prompt for every team. FLYWHEEL first freezes who
will do the work—their expertise, executable methods, data permissions, resources,
time, goals and policies—then compiles one flattened Objective so Argus does not need to
guess feasibility, the venue, novelty evidence, experiment budget, stop conditions or
human gates.

The compiled contract is `argus.flywheel/research-protocol-v2`. It is one
project-level protocol encompassing idea discovery, two-sided challenge, human
selection, evidence, writing, two independent review/revision stages, final integrity,
and outcome capture. Builder/Breaker/Arbiter and the reviewer panel are therefore
project roles, not disconnected top-level products.

The 290 bundled Prompt Packets are a seed coverage baseline for 58 venues. They are
useful for cold-start browsing and regression tests, but they are not the personalized
production Prompt and carry no novelty presumption.

## Team-conditioned Prompt contract

Create or select a TeamProfile before live ideation:

```json
{
  "name": "team or operator alias",
  "expertise": ["knowledge the team can defend"],
  "methods": ["methods/instruments the team can execute reliably"],
  "data_access": ["public or explicitly authorized data/testbeds"],
  "constraints": {
    "people": 2,
    "elapsed_days": 60,
    "forbidden_inputs": ["private reviewer identity", "unlicensed dataset"]
  },
  "goals": {
    "contribution": "desired contribution form",
    "learning_goal": "capability the team wants to build"
  },
  "policy": {
    "ethics": "required approval path",
    "automatic_submission": false
  },
  "training_consent": false,
  "license_basis": ""
}
```

Then call `POST /api/ideation/runs` with `team_profile_id`, `venue_key`, the intended
`deadline_id`, optional `resource_id`/`connection_id`, 3–20 `candidate_count`,
`finalist_count`, source snapshot reference/hash and an operator-defined
`completion_target`. The direct compiler defaults to a target of 10 candidates.
This is a quality-conditioned discovery target, not a minimum quota: returning fewer
ideas or `NO_WINNER` is mandatory when evidence cannot support ten. FLYWHEEL writes:

```text
runtime/ideation-objectives/<objective-sha256>/
  CONDITION_SNAPSHOT.json
  IDEATION_OBJECTIVE.md
```

The snapshot is canonical and immutable. Editing the TeamProfile later does not change
an existing run; new conditions require a new run and hash. This is what makes Prompt
variation meaningful and auditable, rather than random paraphrasing.

The operator may write the completion target freely. FLYWHEEL preserves the original
text and compiles it into `goal_contract.measurable_gates`, `budget`, `stop_criteria`,
valid non-positive outcomes and one deterministic completion rule. “Oral,” “Best
Paper,” “unprecedented,” or “positive result” remain aspirations; they cannot override
evidence, permissions, budget, integrity or human gates. An unknown project-specific
threshold becomes `NEEDS_HUMAN_DECISION`, never an optimistic default.

Source binding is strict: `source_snapshot_ref` and `source_snapshot_sha256` are an
all-or-neither pair, and the digest is exactly 64 hexadecimal characters. Condition
schema v3 preserves the operator's original team-condition statement and records
`source_context.operator_snapshot_bound`, a SHA-256 of the reference,
the content SHA-256 and `fresh_discovery_required`; it also binds the supplied
`preflight_attestations`. The raw reference is deliberately absent from the condition
snapshot and later training JSONL, although the Objective contains a JSON-encoded copy
for runtime location—never place a secret in it. When bound, Argus must verify the
packet bytes and return `BLOCKED` on mismatch, preserving the packet and writing a
separate freshness delta. When unbound, Argus must create and freeze
`SOURCE_SNAPSHOT.json` before proposing candidates.

The generated Objective contains this decision protocol:

1. freeze a primary-source `SOURCE_SNAPSHOT.json` and distinguish official fact,
   forecast, dated observation and inference;
2. `DEBATER_A_BUILDER` aims for up to the target number of defensible candidates from this team's unusual capability
   combinations and records mechanism, falsifier, decisive experiment, nearest work,
   data permission and estimated cost;
3. `DEBATER_B_BREAKER` independently reconstructs the space, attacks collision,
   headroom, feasibility, metric gaming, baselines, ethics/license and venue fit, and
   may add candidates;
4. `ARBITER` compares only frozen outputs, preserves disagreement/ties, selects no more
   than the configured finalist count and may return `NO_WINNER`;
5. after human selection and evidence collection, two independent review stages each
   instantiate five fresh-context reviewers for novelty/collision, methods/statistics/
   falsifiability, resource/schedule, venue/policy, and integrity/ethics/licensing.
   Round two does not inherit round-one scores or conclusions. Missing evidence is
   `score:null`, not a guessed value;
6. bounded revision allows at most two substantive cycles before a from-scratch final
   integrity audit and final human gate.

Builder and Breaker are isolated reasoning tracks/artifacts in the Objective. Do not
describe them as two independent daemon processes unless runtime telemetry proves that
deployment shape.

Every item in `CANDIDATES.json` must contain `candidate_key`, `title`, `problem_gap`,
`core_hypothesis`, `mechanism`, `closest_work`, `differentiation_claim`,
`public_or_authorized_data`, `method`, `strongest_baselines`, `decisive_experiments`,
`falsifier`, `estimated_resources`, `elapsed_time_plan`, `venue_fit`, `risks`,
`ethics_and_license`, `expected_information_gain` and `terminal_recommendation`.
Human scalar labels and pairwise preferences are described in
[PERSONALIZATION_DATASET.md](PERSONALIZATION_DATASET.md).

## Required input envelope

The envelope below is the idea-specific Campaign contract used after or alongside the
TeamProfile-conditioned portfolio. It does not replace the frozen team condition.

```json
{
  "venue": {
    "name": "target venue",
    "edition": 2027,
    "track": "Main / Full Paper",
    "deadline": "ISO date/time or explicit AoE label + evidence status",
    "scope": "official scope summary",
    "policies": ["anonymity, AI-use, ethics, artifact and page-limit checks"]
  },
  "domain": {
    "name": "domain/category",
    "evidence_requirements": ["domain-specific minimum evidence"]
  },
  "idea": {
    "title": "mechanism-oriented working title",
    "problem_gap": "what current primary literature cannot explain or do",
    "mechanism_hypothesis": "falsifiable reason the proposed mechanism should work",
    "method_seed": "minimal method sketch, not a predetermined result",
    "public_data_or_tasks": "existing public tasks/data with version and license; never a new-dataset contribution",
    "decisive_experiment": "the cheapest predeclared experiment that can falsify the mechanism",
    "kill_criterion": "observation that stops or scopes the claim",
    "predicted_observation": "predeclared directional prediction, explicitly not an observed result",
    "baseline_candidates": ["real methods to verify and pin"],
    "source_requirements": ["primary papers", "official proceedings", "official code"],
    "completion_target": "free-form operator aspiration; compiled into measurable gates",
    "candidate_target": 10,
    "finalist_limit": 5,
    "team_conditions": {
      "expertise": ["what this team can defend"],
      "methods": ["what this team can execute"],
      "data_access": ["versioned public or explicitly authorized sources"],
      "constraints": {"people": 2, "elapsed_days": 60}
    }
  },
  "resources": {
    "gpu_count": 1,
    "gpu_model": "detected/configured model",
    "gpu_hours": 24,
    "api_budget": "hard token or currency cap",
    "max_parallel_jobs": 1,
    "wall_clock_deadline": "2026-09-01T18:00:00+08:00"
  },
  "phase": "portfolio or locked"
}
```

Every value that changes scientific interpretation or cost must be concrete before
launch. Never include API keys, bearer tokens, private reviewer material, or unsupported
claims in this envelope.

`wall_clock_deadline` is mandatory for a real start and must be ISO-8601 with an
explicit UTC offset (`Z` or `±HH:MM`); a bare local datetime is rejected. The date in
the stated offset must be no later than `deadline_date` for an `official_confirmed`
target, or `forecast_window_start` for a forecast target. It is the operator's execution
ceiling, not an invented official time of day.

The bundled domain evidence requirements are FLYWHEEL's internal minimum gates, not
official venue policy. They supplement, and never replace, the current CFP/author rules.

## Portfolio Prompt contract

Use `portfolio` before committing to a paper. For a production portfolio, prefer the
TeamProfile-conditioned Objective above. The idea-specific compiled Prompt instructs
Argus to:

1. build a primary-source nearest-work matrix by mechanism, setting, claim and evidence;
2. screen novelty collision, venue fit, public-data access, baseline authenticity,
   ethics and compute headroom;
3. target up to 10 bounded candidates by default and the cheapest decisive pilot for
   each; never pad the output to reach ten;
4. predeclare metrics, expected observations and kill criteria;
5. return `NO_WINNER_YET`, `COLLISION`, `DEFERRED` or `BLOCKED` when no candidate earns
   a real lock;
6. ask for human approval before the winner, resource expansion or irreversible action.

This phase must not write a full paper around an untested idea or optimize for a
positive result.

## Locked Prompt contract

Use `locked` only after human selection. It additionally requires:

- one frozen primary claim and primary metric;
- a minimum meaningful effect threshold;
- a fixed public dataset/task split with version/hash;
- confirmatory random seeds;
- strongest authentic baseline names, official code commits and configurations;
- explicit allowed claim scope and kill/downgrade rules.

Argus then follows a bounded sequence:

```text
HUMAN GATE 0 (conditions + authority)
-> RESEARCH (Builder || Breaker -> Arbiter)
-> HUMAN GATE 1 (selection / NO_WINNER)
-> EVIDENCE -> WRITE -> INTEGRITY CHECK 1
-> INDEPENDENT REVIEW 1 (five fresh contexts)
-> BOUNDED REVISION (at most two substantive cycles)
-> INDEPENDENT REVIEW 2 (five new fresh contexts)
-> FINAL INTEGRITY CHECK from scratch
-> HUMAN GATE 2 -> FINALIZE -> PROCESS SUMMARY
```

Both integrity checks block promotion. Review cannot overwrite raw evidence, failed
runs or negative outcomes. A sealed `NO_WINNER`, novelty collision, killed hypothesis,
resource-infeasible result, insufficient-evidence result, or reproducible negative
result is legitimate research data. External submission remains a human action.

### Freeze through the API

After Portfolio evidence and human selection, freeze the confirmatory contract with:

```http
POST /api/campaigns/{campaign_id}/locked-contract
Content-Type: application/json

{
  "primary_claim": "one falsifiable, scoped claim",
  "primary_metric": "one frozen primary metric",
  "minimum_effect": "minimum practically meaningful effect",
  "data_split": "versioned public train/pilot and untouched confirmatory split",
  "confirmatory_seeds": [101, 202, 303, 404, 505],
  "strongest_baselines": ["method-A@commit", "method-B@commit"],
  "human_approved": true,
  "approval_reason": "named human reviewed the pilot, claim, split and budget"
}
```

This endpoint is a freeze operation, not an execution operation:

| Source lifecycle | Result |
|---|---|
| `idle`, never launched | create v1/v2/... in the same Campaign; identical request returns the existing version idempotently |
| previously launched or otherwise non-idle, but not active | create a new `idle`, `hypothesis_locked` child; preserve the Portfolio source, receipt and files |
| `starting`, `running`, or `draining` | reject with 409; pause safely before promotion |

Each version lives at
`contracts/locked-vN-<contract-sha256>/{OBJECTIVE.md,MANIFEST.json}` and older versions
remain unchanged. The lock manifest records `launch_triggered=false`,
`submission=false`, and `submission_triggered=false`. A later Start re-authenticates
the immutable path, Campaign bindings, version/request/contract/prompt hashes, resource
contract and preflight attestations; it uses the frozen file even if a mutable database
draft was tampered with. Start and external submission remain separate human actions.

Start itself requires a second attributable authorization:

```http
POST /api/campaigns/{campaign_id}/start
Content-Type: application/json

{
  "human_approved": true,
  "approval_reason": "Reviewed the frozen objective, budget, data rights and stop rules.",
  "actor": "operator-alias"
}
```

The approval is frozen in the launch manifest. A failed-launch reconciliation must
reuse the same actor and reason; it cannot turn an old receipt into a newly authorized
mission.

## Minimum artifact contract

Require Argus to preserve, with stable references or hashes:

- condition snapshot, compiled goal contract and attributable human approvals;
- source snapshot and nearest-work/collision matrix;
- independently frozen Builder and Breaker outputs plus Arbiter decision;
- claim–evidence ledger;
- dataset/task provenance, split and licenses;
- environment, code revision, configs, seeds and commands;
- raw run outputs, failed runs and exclusion reasons;
- baseline reproduction and compute-matched comparisons;
- main results, uncertainty, ablations, sensitivity and robustness;
- two integrity reports, two rounds × five independent review reports, revision diffs
  and final limitations;
- reproducibility manifest binding exact code/data/environment/command/config/seeds;
- final paper source/PDF candidate and a process summary.

An artifact is not evidence merely because a file exists. Every material claim must cite
the exact supporting artifact and contradictory evidence must remain visible.
Every material artifact records protocol/compiler and parent version, frozen input,
prompt, condition and source SHA, Argus SHA, model/provider, code/data revision,
environment, command, config, seed, cost, timestamp, actor and its own SHA. New evidence
creates an append-only version; it never rewrites a sealed artifact. “Reproducible” means
an authorized operator can reconstruct the exact inputs and reported output hash, or the
manifest explicitly bounds expected nondeterministic variance.

## Handoff to Argus

FLYWHEEL's preferred route is the authenticated Argus WebAPI. It freezes
`OBJECTIVE.md`, `manifest.json` and `SOURCE_SNAPSHOT.json`, then sends a new daemon
request with an idempotent command ID. A local connection receives a FLYWHEEL-isolated
workspace. A remote connection receives empty `workdir`/`launch_cwd`, so the target
Argus allocates its own workspace; FLYWHEEL does not send a local filesystem path to a
different host. For an explicitly approved local CLI dry-run, the equivalent shape is:

```powershell
argus-skill --daemon --new --continuous --bounded `
  --objective-file <campaign>\OBJECTIVE.md `
  --project-root <campaign>\workspace `
  --life-dir <campaign>\life `
  --backend pi
```

Use the adapter-generated argv (`shell=False`) rather than interpolating shell text.
Pi, Copilot, Codex or another supported provider is a replaceable Argus execution
backend. FLYWHEEL remains the deterministic source of deadlines, receipts, budgets,
evidence state and approvals; the Viewer remains a separate process/context.

The local CLI example can explicitly choose `--backend pi`, but WebAPI
`CreateDaemonIn` cannot: it has no backend field. For the preferred WebAPI route,
configure Pi/Copilot/etc. on the target Argus instance, launch with
`backend=connection-default`, and treat backend identity in the returned/live snapshot
as truth. Local CLI availability never selects or proves a remote Campaign backend.

## Venue/domain-specific generation

`scripts/export_prompts.py` joins every one of the 290 **baseline** idea seeds with its venue
deadline/evidence status and one of the 10 domain evidence contracts. Fixed-deadline
venues use an official date or the earliest forecast boundary. CSCW remains explicitly
`rolling` with `deadline_date=null`; the operator's wall-clock is only an internal
planning cutoff and is never presented as an official submission deadline. It emits:

```text
<venue>/idea-01/OBJECTIVE.md
<venue>/idea-01/MANIFEST.json
<venue>/idea-01/ROUGH_IDEA.md
...
CATALOG.json
CATALOG.md
```

The exporter requires concrete resource caps and does no network/model/Argus call.
It creates five static coverage packets per venue and binds the earliest planning target
for that venue; it does not multiply the catalog by every one of the 85 round/deadline
events and does not condition on a TeamProfile. Every seed manifest records
`personalization_state=seed_coverage_baseline`, `launch_ready=false` and
`requires_team_condition_snapshot=true`.
For a later round, use the API Prompt preview with that specific `deadline_id` (or create
a separately versioned packet) so the frozen manifest names the intended target.
The API Prompt preview is preferable for a live Campaign because it binds the selected
connection, deadline and resource record immediately before launch.

Open `<output>/CATALOG.md` (for the README example,
`runtime/prompt-catalog/CATALOG.md`) to browse all 290 = 58 × 5 Portfolio coverage seeds and
follow links to each Prompt, rough idea and manifest. It is not an `85 × 5` round
expansion and does not imply that any candidate is personalized, novel, locked or
execution-ready. Use `POST /api/ideation/runs` for a team-specific production portfolio.
