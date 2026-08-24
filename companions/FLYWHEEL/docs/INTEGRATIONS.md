# Integrations

Flywheel talks to Argus and research sources through narrow adapters. External
failures remain visible as `error` or `stale_cache`; fixture results are always
`demo` and are never presented as successful live calls.

## Argus WebAPI

```python
from foundry.integrations import ArgusWebClient

client = ArgusWebClient(
    "https://argus-host.example",
    token=token_from_secret_store,
    timeout=10,
)
health = client.test_connection()
projects = client.list_projects()
snapshot = client.snapshot("s-example")
events = client.events("s-example", view="ui")
```

The client implements Argus' real `/api/meta`, `/api/projects`,
`/api/daemons`, snapshot/events, and daemon start/stop contracts. Bearer tokens
are rejected over remote plain HTTP; redirects are not followed, preventing an
authorization header from being forwarded to another host. An environment reference
is valid only for the single server-configured Argus token variable at the exact
server-configured Argus endpoint; both are checked before client construction. Directly
supplied tokens for other endpoints live only in the current process-memory vault. The
current build does not persist an encrypted secret store.
Tokens must never be placed in a URL, log, Prompt, CLI argv, or committed configuration.

Creating or controlling a daemon uses revision-aware command receipts. For a local
connection, Flywheel supplies its isolated Campaign workspace. For a remote connection,
it supplies empty `workdir` and `launch_cwd`, allowing the target Argus to allocate a
valid workspace beneath its own `ARGUS_SKILL_HOME` instead of receiving a path from the
Flywheel host. Prefer `drain=True`; force-stop is an explicit operator action.

`CreateDaemonIn` currently carries `objective`, `name`, `launch_cwd`, `workdir`,
`command_id`, and `expected_revision`; it has no backend field. Consequently the
WebAPI launch path cannot choose Pi, Copilot or another role backend per Campaign.
Flywheel accepts only `backend="connection-default"` for this path and returns 409 for
an apparent override. Configure role backends on the target Argus instance first.
Connection Test records the target's returned runtime revision/release/package,
protocol, snapshot schema and capabilities; Campaign polling then preserves the real
project snapshot, including daemon/role backend fields when Argus reports them.

The compatibility registry distinguishes the official Microsoft origin from explicitly
selected preview origins. Different SHAs must not be treated as the same build merely
because their READMEs describe synchronization. Flywheel requires protocol name
`argus.webapi`, major `1`, and the admission/mission/events capabilities; any observed
minor version or snapshot schema is not a permanent “latest” assertion, stable adoption,
or release pin. Re-run Connection Test and pin an approved full SHA before a real
Campaign.

## Local Argus CLI

```python
from pathlib import Path
from foundry.services import plan_local_argus

plan = plan_local_argus(
    campaign_root=Path("campaigns/iclr-2027/idea-01"),
    objective_file=Path("campaigns/iclr-2027/idea-01/OBJECTIVE.md"),
    backend="pi",  # also copilot, codex, claude, opencode, grok, qoder, dsh
)
```

This returns a dry-run explicit argv with `shell=False`, a dedicated
`workspace/`, and a dedicated `life/`. Execution must be a separate approved
step. Flywheel never launches inside `Argus/` or `argus-skill/` and never updates
a running checkout. This explicitly approved local-CLI plan can contain `--backend`;
it is a different integration path from WebAPI `CreateDaemonIn` and must not be used to
claim that a WebAPI launch applied a backend override.

A local CLI installation neither configures a remote Argus host nor proves which backend
a daemon actually used; the target snapshot is authoritative.

## Locked Campaign Contract API

`POST /api/campaigns/{campaign_id}/locked-contract` freezes an approved confirmatory
contract and never calls Argus. Required request fields are the primary claim/metric,
minimum effect, data split, unique non-negative confirmatory seeds, unique strongest
baselines, `human_approved=true`, and a non-empty approval reason. The Campaign must
already bind an Idea, deadline and enabled resource and pass all preflight/resource
checks, including a wall-clock with explicit offset.

For an idle, never-launched Campaign, a new payload creates the next immutable vN in
that Campaign. The same payload is idempotent. For a Campaign with prior launch/lifecycle
state that is now non-active, the endpoint creates a new idle locked child and records
source/target lineage without changing the source. `starting`, `running`, and
`draining` return 409. Every frozen manifest says no launch and no submission occurred.
Starting a locked child is separate: the start handler re-reads `OBJECTIVE.md` and
`MANIFEST.json` from `contracts/locked-vN-<hash>`, verifies their path, bindings and
hash registry plus the frozen resource/preflight contract, and only then dispatches
`CreateDaemonIn`.

Every Start body must contain `human_approved=true`, a nonblank `approval_reason` and a
nonblank `actor`. The first launch freezes those fields and approval time in the
manifest; reconciliation must submit the exact same authorization. Admission also
rechecks connection protocol/capabilities, verified release truth, resource
enabled/availability, global and per-resource concurrency and the future wall-clock
cutoff. A configured SHA string alone is a reference, not proof that the target actually
runs that release.

Start admission also rejects a missing/naive `wall_clock_deadline`. Its offset-local
date must not exceed the official deadline date for `official_confirmed`, or the
conservative `forecast_window_start` for `forecast`.

## Team-conditioned ideation API

- `POST /api/team-profiles` and `PATCH /api/team-profiles/{id}` manage expertise,
  methods, authorized data, constraints, goals, policy and optional training-export
  consent/license basis.
- `POST /api/ideation/runs` freezes the current profile plus venue/deadline/resource,
  optional source binding, preflight attestations and run target into a content-addressed
  condition snapshot and Objective, and may create an idle `conditioned_ideation`
  Campaign. It never starts Argus. `source_snapshot_ref`/`source_snapshot_sha256` are
  all-or-neither; SHA-256 is exactly 64 hex. Schema v2 hashes the ref in
  `source_context`, keeps raw ref out of the condition/training export, verifies content
  bytes at runtime or blocks, and requires a newly frozen snapshot when unbound.
- `POST /api/ideation/runs/{id}/candidates` imports one immutable candidate set from an
  Argus artifact or human entry, bound to a full artifact SHA-256.
- `POST /api/ideation/candidates/{id}/labels` stores exact seven-dimension scalar labels;
  `POST /api/ideation/runs/{id}/pairwise` stores left/right/tie/abstain preferences.
- `GET /api/datasets/training-export` returns consent/license/redaction-gated JSONL with
  deterministic run/campaign group-safe splits and `X-Automatic-Training: false`.

The full schema and data-governance boundary are in
[`PERSONALIZATION_DATASET.md`](PERSONALIZATION_DATASET.md).
These endpoints do not provide tenant isolation: an untrusted team needs a separate
authenticated deployment/database/runtime/credential set, not merely another profile.

## Source refresh

```python
from pathlib import Path
from foundry.services import sync_sources, differentiate_idea

updates = sync_sources(
    [
        {"kind": "arxiv", "query": "all:agent reliability", "limit": 50},
        {"kind": "openreview", "query": "ICLR.cc/2027/Conference"},
        {"kind": "github", "query": "owner/repository", "limit": 30},
    ],
    cache_dir=Path(".foundry/source-cache"),
    github_token=github_token_from_secret_store,
)
```

- arXiv caches the same query for one day and spaces live calls by at least
  three seconds.
- OpenReview uses API2 `/notes?content.venueid=...`; configure the accepted
  venue id, not a guessed display name.
- GitHub uses REST conditional requests (`ETag`) and reports rate-limit reset
  metadata. A token is optional and is sent only in the Authorization header.
- Every update reports added/removed/changed ids and a Chinese difference
  summary. `differentiate_idea()` adds deterministic overlap terms, nearest
  items, source-snapshot changes, and a refresh Prompt. Its `novelty_risk` is a
  lexical triage label, never a novelty score or proof.

For an offline demo, pass explicit fixture paths through `demo_fixtures`. The
response status will be `demo` and says that no external service was called.

## Read-only Argus release monitoring and isolated staging

```python
from foundry.services import inspect_release

status = inspect_release(
    "https://github.com/microsoft/ArgusAgent.git",
    reported_release={"commit_sha": "<reported-sha>"},
)
```

The monitor runs only `git ls-remote`. It compares remote, connected/reported,
and registered stable SHAs and can expose a candidate diff to the UI. It never
opens or modifies an existing checkout.

The default monitored origin is the official Microsoft `ArgusAgent` repository. The
`lbx154/Argus` preview origin may be inspected only when explicitly selected. Registry
records retain repository URL and exact ref/SHA, so a SHA from one origin is never
silently reinterpreted as a build from the other.

`POST /api/releases/inspect` atomically publishes the latest inspection to
`FLYWHEEL_DATA_DIR/releases/registry.json`, so the live Release view can show
the actual remote candidate. The merge updates only remote-inspection fields;
existing `stable_sha`, `canary_sha`, nested stable/canary records, and other
human-controlled registry fields are preserved. An invalid existing registry
fails closed and is not overwritten.

After a human has inspected that result, an exact candidate can be staged with:

```http
POST /api/releases/stage
Content-Type: application/json

{
  "repository": "https://github.com/microsoft/ArgusAgent.git",
  "ref": "refs/heads/main",
  "expected_sha": "<full-40-or-64-character-sha>",
  "confirm_isolated_stage": true
}
```

The endpoint resolves the same exact ref with read-only `git ls-remote` first
and rejects a moved or mismatched SHA. It then creates only
`FLYWHEEL_DATA_DIR/releases/staging/<full-sha>/source`, initializes a new empty
repository, fetches the ref, and checks out the exact SHA detached. The target
is content-addressed and idempotent: only a complete manifest for the same
repository/ref/SHA may be reused; partial, linked, mismatched, or unreadable
paths fail closed and are never overwritten.

Git is invoked with explicit argv, `shell=False`, a timeout, prompts disabled,
global/system Git configuration disabled, an empty template, disabled hooks,
and local transport disabled. No staging command contains `pull` or `reset`.
Failures remain in `releases/attempts/` and, once a stage directory exists, in
its `diagnostics.json`; the service does not clean away forensic evidence.

The generated `manifest.json` records repository, ref, immutable SHA,
timestamps, checkout verification, and explicit states:

- tests: `not_run`;
- adoption: `not_adopted`;
- daemon: `not_started`;
- running campaigns mutated: `false`.

Staging is therefore not an upgrade. Tests, canary, stable adoption, and use by
new campaigns remain separate human-approved workflows. Existing campaigns, parent
Argus package files and any sibling `argus-skill` checkout are outside this integration's
write scope; only the configured companion runtime directory is writable.

## GPU resources

`probe_resources()` runs a read-only `nvidia-smi` CSV query with an explicit
argv and timeout. Missing drivers, timeouts, non-zero exit, and malformed output
return `available: false`; they do not produce synthetic GPU inventory.

## Independent Viewer

Queue a review through the server so evidence is frozen from the attached Argus project
rather than trusting a browser-supplied filesystem path:

```http
POST /api/campaigns/{campaign_id}/review
Content-Type: application/json

{
  "reviewer_kind": "methods_reviewer",
  "rubric": {"weights": {"technical_quality": 2, "empirical_rigor": 2}},
  "human_approved": true,
  "actor": "operator-alias",
  "approval_reason": "Challenge the frozen methods claims before promotion."
}
```

For one shared immutable evidence snapshot and 2–5 independently queued reviewers:

```http
POST /api/campaigns/{campaign_id}/review-panel
Content-Type: application/json

{
  "reviewer_kinds": [
    "novelty_reviewer",
    "methods_reviewer",
    "resource_reviewer",
    "venue_reviewer",
    "integrity_reviewer"
  ],
  "rubrics": {"novelty_reviewer": {"nearest_work_required": true}},
  "human_approved": true,
  "actor": "operator-alias",
  "approval_reason": "Run the independent pre-lock panel within the approved spend."
}
```

The route reads the target's artifact index, selects only existing allowlisted
`text|markdown|json|table` paths, obtains previews through the Argus artifact endpoint,
and freezes `viewer/evidence-snapshots/<sha256>/EVIDENCE_SNAPSHOT.json`. Defaults are at
most 24 artifacts, 64 KiB per preview and 512 KiB total. Each preview and the canonical
snapshot carry SHA-256. The panel route reuses those exact frozen bytes for every member
while assigning each request a fresh evaluator process/workdir; reports and disagreements
remain separate and the route does not manufacture an aggregate acceptance probability.

Run one independent evaluator attempt:

```powershell
.\.venv\Scripts\python.exe -m foundry.workers.viewer_worker `
  --queue-dir runtime/viewer `
  --once `
  --evaluator-command-json '["reviewer-adapter","--backend","pi"]'
```

`reviewer-adapter` is a separately deployed JSON stdin/stdout adapter. It may use Pi,
Copilot, Codex, or another configured reviewer model, but it must not be the Campaign
process. The queue atomically claims a request into `processing`; stale claims can be
recovered without two workers evaluating the same inbox file concurrently. Before
calling the evaluator, the worker authenticates the inline evidence snapshot and every
preview hash, creates a fresh work directory, and records PID, Campaign PID, timestamps,
exit code, and output SHA-256. Credentials are forbidden in the command argv; the
adapter must obtain them from its own secure credential store.

The output schema and 1–10 venue-calibrated evidence-readiness dimensions are
documented in `prompts/VIEWER_PROTOCOL.md`. Without a configured evaluator, or with an
empty evidence snapshot, the worker returns no numeric score; it never fabricates a
review score.

## Post-submission outcome and rebuttal API

`POST /api/outcomes/submissions` records a human-entered paper version and one or more
pseudonymous reviewer opinions, scores/confidence/questions and a decision. It is not a
submission connector. `POST /api/outcomes/submissions/{id}/follow-up` requires actor and
approval reason, freezes a content-addressed rebuttal Objective and creates an idle
follow-up Campaign. Starting that Campaign and submitting any response remain separate
human actions. Per-submission JSONL is available only from
`GET /api/outcomes/submissions/{id}/training-export` when consent, review-use rights and
redaction gates all pass.

## Verification

```powershell
# Run from the Argus repository root.
Set-Location .\companions\FLYWHEEL
.\.venv\Scripts\python.exe -m pytest backend
```

The integration tests use fake transports and explicit fixtures; they do not
contact Argus, arXiv, OpenReview, GitHub, or a model provider.
