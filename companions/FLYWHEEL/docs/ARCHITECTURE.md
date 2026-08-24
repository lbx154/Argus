# Architecture

```text
Browser
  |
  | REST + WebSocket
  v
Flywheel API ----------------------------------------------------------------+
  |               |             |              |              |             |
SQLite         Scheduler    Condition/Prompt  Viewer queue  Source radar  Dataset export
ledger         + reminders  compiler + hashes + worker      primary APIs  gated JSONL
  |               |             |              |              |             |
  +---------------+-------------+--------------+--------------+-------------+
                                |
                                v
                    Argus connection adapters
                       |                 |
                  local WebAPI      remote WebAPI
                  + CLI dry plan    HTTPS + bearer token
                       |                 |
                       +--------+--------+
                                v
                     isolated Argus projects
```

The WebAPI branch is the implemented Campaign launch path. The CLI adapter only builds
a redacted dry-run argv (`dry_run=true`); Flywheel does not currently execute that plan.

## Why the platform does not use Pi as its control plane

Pi, GitHub Copilot CLI, Codex CLI and similar tools are model/tool execution backends.
Argus already owns their lifecycle and maps them onto Manager, Planner, Engineer and
Reviewer roles. The current Argus `CreateDaemonIn` WebAPI contract does not accept a
backend override: a daemon with an objective starts on the target Argus instance's
preconfigured default. Flywheel therefore does not offer a launch selector that would
only look effective; a non-default per-launch backend request is rejected, and Flywheel
stores the backend/role truth actually reported in later Argus snapshots.
Durable scheduling, deadlines, evidence, approvals and audit state remain deterministic
application concerns in Flywheel.

This separation still allows, for example, an Argus instance configured with Engineer
on Pi and an independent Viewer adapter on Copilot, without coupling application
correctness to either provider. Role switching must be configured and verified on the
target Argus instance before Flywheel launches a Campaign.

The release registry distinguishes the official Microsoft origin from explicitly
selected preview origins. A README sync claim does not make different SHAs byte-identical
or interchangeable. Repository observations are point-in-time compatibility evidence,
not a permanent latest-version claim or an adopted release. At runtime a connection
probe persists the target's returned revision, release/package identity, protocol,
snapshot schema and capabilities.
Campaign polling separately stores the actual project snapshot; it does not infer
backend identity from locally installed CLIs.

Persisted `env:` credential references are restricted to the single server-configured
Argus token environment name **and its exact normalized server-configured endpoint**.
The variable name or endpoint mismatching fails before a network client is constructed,
so client requests cannot select another process environment variable or forward the
Argus token to another host. A literal Bearer is process-memory-only and mutually
exclusive with `token_env`.

## Isolation model

```text
flywheel runtime root/
  ideation-objectives/<objective-sha256>/
    CONDITION_SNAPSHOT.json immutable TeamProfile/venue/resource/run conditions
    IDEATION_OBJECTIVE.md   Builder/Breaker/Arbiter objective
  campaigns/<campaign-id>/
    OBJECTIVE.md
    manifest.json
    SOURCE_SNAPSHOT.json
    contracts/
      locked-vN-<contract-sha256>/
        OBJECTIVE.md          immutable human-approved confirmatory objective
        MANIFEST.json         bindings, request/contract/prompt hashes and no-launch flags
    workspace/             research worktree
    life/                  isolated Argus lifecycle state
    reviews/               campaign review material
  viewer/
    evidence-snapshots/<sha256>/EVIDENCE_SNAPSHOT.json
    inbox/                 durable Viewer requests
    processed/             consumed requests
    outbox/                independent Viewer results
    work/<request-id>/...  fresh evaluator work directories
  rebuttal-objectives/<objective-sha256>/
    REBUTTAL_OBJECTIVE.md  human-authorized, no-start/no-submit follow-up objective
  releases/
    registry.json          atomically refreshed remote-inspection record
    attempts/              preserved stage attempts/rejections
    staging/<full-sha>/
      manifest.json        stage-only provenance and gate states
      diagnostics.json     preserved failure/command diagnostics when applicable
      source/              detached exact-SHA checkout; never a live Campaign
  prompt-catalog/
    CATALOG.md             browsable 58 × 5 seed coverage index
    CATALOG.json           machine-readable 290-seed packet index
```

The platform never uses an existing Argus repository as a campaign workdir. It never
runs `git pull` against a live or dirty checkout. Release inspection is a read-only
`git ls-remote` comparison whose registry update is atomic and preserves human
stable/canary fields. An explicitly confirmed stage first verifies that the remote ref
still equals the supplied full SHA, then creates only
`releases/staging/<sha>/source` and performs a detached exact-SHA checkout. It does not
run tests, canary, a daemon or adoption. A Campaign manifest records either the chosen
SHA or `release_pinned=false`.

## Conditioned ideation and annotation lineage

TeamProfile rows are mutable operator records, but every ideation run copies the
scientifically relevant fields into canonical `condition_snapshot_json` and hashes the
exact bytes. Later profile edits never rewrite an old run. An optional
`conditioned_ideation` Campaign points to that run and starts in `idle`; creating the
run does not call Argus.

Condition schema v3 preserves the operator's original team-condition statement and also
binds `preflight_attestations` and `source_context`. A supplied
source ref/content digest must be an all-or-neither pair with exactly 64 hex characters
for the content SHA-256. The condition contains `operator_snapshot_bound`,
`reference_sha256`, `content_sha256` and `fresh_discovery_required`, never the raw ref.
The Objective carries a JSON-encoded raw reference for runtime location, so secrets are
forbidden there; Argus must hash the located bytes and enter `BLOCKED` on mismatch. If
no packet is bound, source discovery and a frozen `SOURCE_SNAPSHOT.json` precede
candidate generation.

`team_profile_id` is provenance, not authorization. The database is not tenant-aware and
the API has no auth/RBAC or tenant-scoped secrets. Security isolation between untrusted
teams is deployment-level: separate database, runtime root, process and credentials.

Argus or a human imports one immutable candidate set per run with an artifact SHA-256.
Scalar labels and pairwise preferences refer to candidate IDs and keep pseudonymous
labeler, rationale, consent, license and redaction state. JSONL rows embed the frozen
condition and exact candidate JSON, so a model can learn “which idea fits which team”
rather than memorizing a venue-only seed. Deterministic group split keeps all rows from
one ideation run together. Outcome reviews use source Campaign as their group.

Selecting one candidate creates a separate idle Campaign plus an immutable
`conditioned_campaign_bindings` receipt. It binds the ideation run, condition, parent
Objective, candidate portfolio, candidate record, compiled input, candidate Prompt and
content-addressed Objective path. Start recomputes those edges and bytes. A Research
Episode that names a run/candidate must bind this actual candidate execution Campaign;
the earlier ideation Campaign is recorded only as `ideation_source`.

No database flag starts training. Episode/outcome export additionally re-verifies a
frozen `conditioned_candidate_research` receipt, or a valid rebuttal that traces back to
one, at detail, selection, snapshot and export time. Seed, manual, unbound and
pre-execution ideation Campaigns remain archiveable but are never training-eligible.
Scalar/pairwise candidate decisions use a separate conditioned-ideation proof so honest
negative and unlaunched candidates are retained only when the frozen condition,
Objective, manifest, portfolio and candidate-record hashes still match. Consent,
nonblank license basis and redaction/pseudonymization remain additional mandatory gates;
downstream training is an external human-approved process.

## Locked Contract lifecycle

Locking and launching are deliberately separate state transitions:

```text
idle, never launched
  -- POST locked-contract --> same Campaign / immutable vN / no Argus contact

launched, now non-active
  -- POST locked-contract --> new idle hypothesis_locked child
                              source Campaign and launch receipt unchanged

starting | running | draining
  -- POST locked-contract --> HTTP 409

locked child
  -- POST start with human approval --> authenticate immutable files and bindings,
                                      then CreateDaemonIn
```

Each different approved contract creates `locked-vN-<hash>` without modifying older
versions; the exact same request is idempotent. A lock manifest explicitly records
`launch_triggered=false`, `submission=false`, and `submission_triggered=false`.
At child start, Flywheel rejects a path outside the Campaign contracts root, missing or
corrupt files, Campaign/binding mismatch, version/request/contract mismatch, prompt hash
mismatch, changed resource contract or changed preflight attestations. The mutable
database objective is not trusted in place of the frozen `OBJECTIVE.md`.

Production Start admits only a re-authenticated `conditioned_ideation`,
`conditioned_candidate_research`, or a `rebuttal_follow_up` whose source is a valid
conditioned candidate. Seed, manual and unbound Campaigns remain non-executable. The
first Start also rechecks a launch-compatible online connection, enabled/available
resource, hard capacity limits, global and per-resource concurrency, future wall-clock
cutoff and preflight attestations inside the admission path. `human_approved`, reason,
actor and approval time are frozen in the manifest; a reconciliation must reproduce
them exactly. Once a launch receipt exists, launch-critical connection/resource/
objective/config fields cannot be patched in place.

## Local and remote workspace semantics

A local WebAPI launch receives the Flywheel-owned Campaign `workspace/` as both workdir
and launch cwd (`workspace_mode=foundry_local_isolated`). A remote WebAPI launch sends
empty `workdir` and `launch_cwd` (`workspace_mode=target_argus_default`), causing the
target Argus instance to allocate beneath its own `ARGUS_SKILL_HOME`. Flywheel retains a
local evidence/manifest directory, but never passes a Windows or host-local path to a
different machine as if it were valid there.

## Viewer evidence snapshot

Review admission ignores arbitrary client-supplied local paths. The server selects only
existing `text`, `markdown`, `json` and `table` artifacts from the target Argus index,
fetches their previews through the Argus API, enforces per-artifact and total byte caps,
hashes each preview, and writes canonical `EVIDENCE_SNAPSHOT.json`. The queue contains
the authenticated inline snapshot; the independent worker verifies its SHA-256 before
an evaluator sees it. Empty or tampered evidence yields no score and cannot certify a
Campaign.

## Wall-clock admission

Conference seed dates are date-level evidence, so Flywheel never invents a time of day.
Every real start requires a timezone-aware ISO-8601 `wall_clock_deadline` with an
explicit UTC offset. Its date in that stated offset must be on or before:

- `deadline_date` for `official_confirmed`; or
- `forecast_window_start` for `forecast`.

This is an admission ceiling, not an assertion that the conference closes at that
operator-selected wall-clock time. Rolling venues use an explicitly labeled internal
cutoff.

## Event contract

Flywheel events are append-only SQLite rows carrying an id, topic, event type, creation
time, entity type/id, severity and a redacted JSON payload. External sequence,
correlation or causation identifiers may be preserved inside that payload when the
adapter supplies them; they are not first-class columns in the current schema. API
keys, full credentials and unredacted tool payloads are forbidden.

Core families:

```text
schedule.*  campaign.*  stage.*  claim.*  evidence.*  experiment.*
review.*    integrity.* approval.* resource.* release.* alert.* control.*
ideation.*  submission.* rebuttal.*
```

## Failure semantics

- Connection failures preserve the last known snapshot and mark it stale.
- A live PID with stale logs/evidence is `NEEDS_ATTENTION`, not healthy.
- API commands use idempotency keys and expected revisions.
- Rate-limit/reset metadata is surfaced to the operator/caller and must be honored;
  deterministic failures are not blindly retried.
- Viewer failure cannot mutate campaign evidence or certify a paper.
- Integrity failure quarantines manuscript promotion until an explicit resolution is
  recorded.
