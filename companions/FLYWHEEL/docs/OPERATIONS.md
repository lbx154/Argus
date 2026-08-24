# Operations, safety and recovery

## Deployment boundary

Flywheel source is packaged at repository-root `companions/FLYWHEEL`, but it does not
import or mutate the parent Argus package. Its default `runtime/` is companion-owned,
Git-ignored state and must never overlap an Argus project worktree or package directory.
For a shared, read-only or production checkout, set `FLYWHEEL_DATA_DIR` and
`FLYWHEEL_DATABASE_PATH` to an access-controlled external volume. Release inspection is
read-only. Explicit staging fetches only a confirmed exact SHA into Flywheel's configured
runtime release directory; it never pulls/resets an existing checkout and never adopts
a release.

The API has no built-in multi-user authentication. Keep development endpoints on
`127.0.0.1`. For network access, require an authenticated TLS reverse proxy, firewall
allow-list and server-side secret injection. Never publish port 8743 directly.

TeamProfile is a portable conditioning record, not a SaaS tenant or security principal.
The current build has no user authentication, RBAC, tenant-scoped database policy or
per-tenant secret isolation. Teams that do not fully trust one another must use separate
Flywheel deployments, databases, runtime directories and credentials rather than sharing
one instance and relying on `team_profile_id` as an access-control boundary.

Release inspection/staging initiates outbound Git network connections to the supplied
remote host. In a shared or networked deployment, enforce an egress/repository-host
allow-list (for example, only the approved Argus GitHub origin) at the proxy/network
boundary. The application rejects local/file paths and credentials embedded in URLs,
but host authorization remains an operator policy.

## Start and stop

Native development uses two terminals from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn foundry.app:app --app-dir backend\src --host 127.0.0.1 --port 8743
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5175
```

Stop each foreground process with `Ctrl+C`; do not kill an attached Argus process as a
substitute for Flywheel shutdown. Docker binds only `127.0.0.1:8080` by default:

```powershell
docker compose up --build
docker compose down
```

Inside Docker, `127.0.0.1` is the container itself. On Docker Desktop, a host Argus API
normally needs `host.docker.internal:<port>` or an explicitly routed HTTPS hostname.
Test that route before Campaign approval.

The default Compose file does not pass through an NVIDIA runtime/device. A GPU probe in
that container may correctly return unavailable; either configure a supported NVIDIA
container runtime or create a manually verified resource contract. Never substitute
demo GPU inventory.

## Runtime data and secrets

| Path | Purpose | Backup priority |
|---|---|---|
| `runtime/flywheel.db` plus `-wal`/`-shm` while live | control state, reminders, events, source/Viewer metadata | critical |
| `runtime/ideation-objectives/<sha>/` | immutable TeamProfile condition snapshot and conditioned Argus Objective | critical for personalized-candidate provenance |
| `runtime/campaigns/<id>/` | frozen launch Prompt/manifest/source snapshot, immutable `contracts/locked-vN-<hash>/` and isolated Argus work/life/reviews | critical |
| `runtime/prompt-catalog/` when exported | browsable `CATALOG.md` plus 290 = 58 × 5 seed packets bound to each venue's earliest target | planning coverage only; not personalized ideas or proof of locked/executed papers |
| `runtime/viewer/` | evidence snapshots, inbox/processing/processed requests, outbox and fresh evaluator workdirs | critical for review provenance |
| `runtime/rebuttal-objectives/<sha>/` | frozen human-authorized rebuttal objectives; associated Campaign remains idle until Start | critical for outcome/rebuttal provenance |
| `runtime/source-cache/` | rate-limit-aware source cache | useful; reproducible from sources only when still available |
| `runtime/releases/` | inspected registry, stage manifests, exact-SHA checkouts and preserved diagnostics | critical for release provenance; treat as source code, with credentials forbidden |

Bearer tokens supplied directly are process-memory only and disappear on restart.
Environment-backed tokens remain in the server environment. Neither belongs in
SQLite, Prompt text, manifest, browser state, URL, logs or evaluator argv.

Training-export consent is record-specific and revocable only by a documented data
governance process; copying an already exported file creates another governed artifact.
Store JSONL outside public/shared folders, preserve its license basis and split fields,
and never join aliases back to reviewer identities. A successful export is not legal or
ethical clearance for training.

For a consistent backup, first stop Flywheel and its Viewer worker, then copy the entire
`runtime/` directory to an access-controlled, versioned destination. Merely copying
`runtime/flywheel.db` while the process is live can omit WAL state. Restore only while Flywheel is
stopped, keep the original backup, then run `scripts/check.ps1` and inspect `/api/health`
before reconnecting to Argus. Memory-only secrets must be supplied again.

With Docker Compose, `/app/runtime` lives in the `flywheel-runtime` named volume rather
than the repository's `runtime/` directory. Stop the stack and back up that volume with
your approved volume-backup procedure. `docker compose down` preserves it;
`docker compose down -v` deletes it and must not be used as an ordinary shutdown.

## Scheduling and time

- The planning window is inclusive, but actual stored targets currently range from
  2026-08-25 through 2027-08-19.
- API-created reminders and `scheduled_for` values require an explicit UTC offset and
  are normalized to UTC. Conference `AoE` remains a deadline label, not the server's
  local timezone.
- A real Campaign `wall_clock_deadline` also requires an explicit offset. Start rejects
  a missing/naive value, a stated-offset date after an `official_confirmed` deadline
  date, or a forecast cutoff after `forecast_window_start`.
- Default reminders are D-180/90/30/14/7/2. The research pipeline begins around D-180;
  D-30 is the adversarial-review/convergence sprint, not the default zero-to-paper
  starting point.
- A due scheduled Campaign becomes `awaiting_approval`; it is not automatically
  launched or submitted.
- The current server durably marks due reminders and emits in-app events. Browser
  notifications are reliable only while the page is open; email/webhook delivery is
  not implemented in this release. Import `/api/calendar.ics` into an external calendar
  or add a tested delivery adapter for unattended notification.

## Safe operating sequence

1. Run `scripts/check.ps1` and save its output.
2. Read the demo-data banner; never treat demo hardware, SHA, scores or Campaigns as
   observations.
3. Create or review the TeamProfile: expertise, executable methods, authorized data,
   constraints, goals and policy. Treat the 290 catalog entries as seed coverage only.
4. Re-probe resources, add hard budgets and select the intended connection.
5. Recheck official deadline/policy pages and record the source snapshot.
6. Create a conditioned ideation run and inspect its immutable condition hash,
   Builder/Breaker/Arbiter Objective and completion target. A supplied source ref and
   exact 64-hex SHA-256 must appear together; ensure the raw ref has no secret and accept
   the verify-or-BLOCKED rule. Without a binding, source freeze must precede candidates.
   Creating this run or its idle Campaign must not contact Argus.
7. Inspect the compiled Prompt and manifest preview; accept negative outcomes. For
   confirmatory work, POST the human-approved Locked Contract. Verify that freezing
   produced immutable vN files and explicitly did not launch or submit anything.
8. Inspect the official Microsoft Argus remote ref. If preview is intentionally chosen,
   record that separate origin. If a new candidate is needed, explicitly stage the matching
   full SHA, then run a separately approved test/canary procedure. Verify or explicitly
   waive the Argus release pin for non-production work.
9. Configure and verify role backends on the target Argus instance. Do not request a
   per-launch Pi/Copilot override through `CreateDaemonIn`; use
   `backend=connection-default` and inspect the later target snapshot.
10. Record `human_approved=true`, a nonblank reason and actor, then launch one isolated
   Campaign. If the source
   Portfolio was previously launched and is now paused/non-active, lock creates a new
   child; start the child, never rewrite the source receipt/workspace.
11. Confirm the returned Argus project ID, frozen contract hash, workspace mode and
   actual reported backend before scaling concurrency. For a remote target, workspace
   allocation belongs to that target; do not expect a Flywheel-host path in the receipt.
12. Run independent Viewer only against server-frozen, bounded allowlisted evidence
   snapshots. Keep panel members and disagreements separate.
13. Record post-submission reviewer/outcome data only after pseudonymization. Create a
   rebuttal follow-up as idle, then apply a new human Start gate if work should proceed.
14. Keep external paper/rebuttal submission and any model training as separate human
   actions.

## Failure and recovery matrix

| Symptom | Safe interpretation | Recovery |
|---|---|---|
| API/Flywheel restarts | SQLite and file queues are durable; process-memory tokens are not | Restart Flywheel, re-supply memory secrets, inspect pending reminders/events before taking action |
| TeamProfile changed after ideation | Existing condition snapshot intentionally remains unchanged | Create a new ideation run; never rewrite the old condition/objective or relabel it as generated under the new profile |
| Another team can see shared-instance data | TeamProfile is not tenant isolation; current API has no auth/RBAC | Stop sharing the instance. Deploy separate database/runtime/credentials behind authenticated TLS for untrusted teams |
| Argus connection fails | Last snapshot may be stale; daemon truth is unknown | Do not launch a replacement. Check network/TLS, query Argus directly, then refresh the existing Campaign |
| PID alive but no evidence/log progress | Operational liveness without scientific progress | Mark/keep `needs_attention`; inspect blocker, budget and kill criterion rather than resetting progress |
| Start request times out or returns no project ID | Argus may have accepted the idempotent command | Do not create a second Campaign. Compare `manifest.json` `launch_command_id` with Argus projects/events. When the existing Campaign is `failed`/`needs_attention` with no attached project, a start reconciliation reuses the same command ID, objective hash, connection and launch arguments; it never creates a new receipt |
| Repeated start returns HTTP 409 | The Campaign is active, has attached remote state, or is not in a safely reconcilable state | Refresh Campaign/Argus truth. Never clear the command ID; use reconciliation only after verifying the frozen packet and remote state |
| Locked Contract returns HTTP 409 while active | `starting`/`running`/`draining` cannot be promoted in place | Pause safely and refresh state. A prior-launched non-active Portfolio promotes into a new child; preserve the source Campaign and launch receipt |
| Locked child Start reports missing/corrupt/mismatch | Immutable path, bindings, version/request/contract/prompt hash, resources or preflight no longer authenticate | Do not fall back to the mutable database objective. Preserve both files, investigate tampering/corruption, then create a new reviewed contract version if appropriate |
| Start rejects `backend` override | `CreateDaemonIn` has no per-launch backend field | Configure the backend on the target Argus instance, retest its connection, use `connection-default`, and verify the actual live snapshot |
| Remote launch shows empty workdir/cwd | Expected target-owned workspace allocation, not missing local setup | Verify the target Argus project reports its allocated workspace; never patch in a Flywheel-host filesystem path |
| Start rejects wall-clock cutoff | Value is missing/naive or later than the official date/forecast-window start | Supply an ISO-8601 value with explicit offset and choose a conservative execution ceiling; do not change conference seed evidence to bypass admission |
| Viewer evaluator absent | No independent score exists | Configure the adapter and requeue; `awaiting_evaluator`/`overall:null` is correct |
| Viewer evidence snapshot is empty/tampered | No allowlisted evidence can be authenticated | Keep score null, restore/freeze eligible artifact previews through the Argus API, then create a new review request |
| Viewer evaluator fails or times out | Campaign evidence is not certified or mutated | Preserve request/result/error evidence, fix the adapter, run a new request with fresh context |
| Source is offline/rate-limited | Cache may be stale and novelty risk may have increased | Honor retry/reset metadata, label cache age, do not silently lock an Idea |
| Forecast deadline changes | Schedule may be unsafe | Update through a new sourced snapshot, surface the delta, and require human re-approval; never rewrite a locked manifest silently |
| Integrity gate fails | Manuscript promotion is quarantined | Preserve failure evidence, resolve each issue, and rerun final integrity from scratch |
| Dataset export omits a label/outcome | One or more consent, license or redaction gates are absent | Record a lawful basis and explicit confirmation only if true; never weaken the gate or copy private/unlicensed text into JSONL |
| Rebuttal follow-up is idle | Expected no-start/no-submit state | Review the frozen objective and evidence, then use the ordinary explicit Start gate if authorized; submission remains external |
| New Argus upstream SHA appears | Candidate only; running Campaign remains pinned | Inspect read-only, explicitly stage the matching full SHA, then run separate tests/canary. Adoption is not automated and may apply only to new Campaigns |

## Viewer operations

Start a single queue pass from the project root:

```powershell
.\.venv\Scripts\python.exe -m foundry.workers.viewer_worker --queue-dir runtime\viewer --once
```

For real evaluation, configure `FLYWHEEL_VIEWER_COMMAND_JSON` as an explicit JSON argv.
The adapter reads credentials from its own environment and writes one JSON object to
stdout. Preserve request ID, evaluator PID, fresh workdir, exit code and stdout hash.
Run it only against the server-frozen content-addressed evidence snapshot. The worker
atomically claims requests; a stale processing claim may be recovered, but two workers
must not evaluate the same inbox item concurrently. An internal 1–10 score is
evidence-readiness feedback, never acceptance probability.

## Source and release operations

arXiv refreshes use a daily cache and request spacing; OpenReview requires the exact
API2 `content.venueid`; GitHub uses conditional requests and exposes rate-limit state.
Record `fresh`, `cache`, `unchanged`, `demo` or `error` without collapsing them into a
single success badge. Lexical idea differentiation is a triage aid only.

Release inspection may run `git ls-remote <repo> refs/heads/main` and atomically refresh
the registry while preserving human stable/canary fields. The default origin is the
official `microsoft/ArgusAgent`; `lbx154/Argus` is an explicitly selected preview origin.
Explicit staging re-verifies the full SHA and repository/ref identity, then uses a new
content-addressed directory for init/fetch/detached checkout. It must never
pull/reset/checkout an existing worktree. The resulting manifest says
tests/canary/adoption have not run; those gates remain separate and manual.

Treat every remote SHA, protocol version and snapshot schema as a dated audit
observation. Different origin/SHA pairs are different builds. The connection's current
`/api/meta`, separately tested/canary-approved origin+SHA, and live project snapshot are
operational truth. Local CLI availability does not configure a target Argus daemon.
