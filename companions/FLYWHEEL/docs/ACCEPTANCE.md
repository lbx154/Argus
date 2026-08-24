# Acceptance and evidence checklist

This document separates what the offline test suite proves from what an operator must
verify against real Argus, real providers and current conference pages. A green local
build is not evidence of conference acceptance, novelty, positive results, an Oral, or
successful external connectivity.

## One-command offline acceptance

From the project root:

```powershell
.\scripts\check.ps1
```

The command is intentionally safe for an existing Argus installation. It:

1. validates the versioned catalog without network access;
2. runs backend unit/API/integration tests with temporary SQLite/runtime directories
   and mocked external transports;
3. exports all 290 = 58 × 5 resource-bound seed Prompt Packets into a pytest temporary
   directory and checks `CATALOG.json`, browsable `CATALOG.md`, manifests and links;
4. creates a production frontend build;
5. runs an in-process API smoke check.

It does **not** start, stop, pull, reset, probe, or send a Prompt to real Argus. It does
not call arXiv, OpenReview, GitHub or a model provider, generate real personalized
candidates, execute experiments, submit a paper/rebuttal or train a model. Expected
catalog evidence is:

```text
58 CCF-A venues
85 Full/Regular Paper deadline events in the inclusive planning window
28 official_confirmed + 57 forecast
290 baseline idea seeds (exactly 5 per venue; not personalized recommendations)
10 domain evidence contracts
```

`scripts/audit_catalog.py` fails if counts, uniqueness, date bounds, evidence status,
official/historical source URLs, forecast confirmation gates/intervals, per-venue idea
ranks, kill criteria or domain-contract coverage regress.

## Requirement traceability

| Requirement | Automated evidence | Mandatory human/external evidence |
|---|---|---|
| Isolation from existing Argus | CLI launch-plan test proves dedicated `workspace/` and `life/`; release tests confine exact-SHA staging below Flywheel data and reject local/file/path inputs without mutating an existing checkout | Record `git status --short` for each existing Argus/argus-skill checkout before and after deployment; verify no Flywheel process uses those paths |
| Complete seeded horizon | catalog audit; dashboard/API tests; `.ics` smoke | Recheck every forecast on its official venue page before lock/submission; the seed universe is 58 CCF-A venues, not all conferences worldwide |
| Five baseline seeds and Prompt per venue | catalog audit; Prompt invariant/hash tests; exporter verifies 58×5 distribution, all 290 `CATALOG.md` links and CSCW rolling distinction | Expert screens novelty, feasibility, ethics and venue fit; a seed is neither a TeamProfile-conditioned recommendation nor a novelty claim; 290 is not 85 rounds × 5 |
| Team-conditioned ideation | tests compare two TeamProfiles, condition schema v3/objective hashes, preserved operator statement, immutable files, all-or-neither exact-64-hex source binding, hashed reference/content verification, preflight binding and idle Campaign creation | Operator verifies actual expertise, methods, data rights, constraints, goals, policy and source packet; raw ref contains no secret; TeamProfile is not a multi-tenant security boundary |
| Human candidate annotation | candidate schema/import immutability, exact scalar-dimension and pairwise-validation tests | Pseudonymous labelers review evidence, retain disagreement and justify shortlist/revise/reject/abstain or pairwise decisions |
| Consent-gated dataset export | tests prove run/record consent + license + redaction conjunction, schema IDs, deterministic group-safe split and `X-Automatic-Training:false` | Data steward audits license, privacy, leakage, duplicates and institutional/venue policy before any separate training run |
| Locked Contract lifecycle | tests prove no Argus contact/no launch/no submission at lock, in-place immutable vN for never-launched idle, child promotion for prior-launched non-active, active 409, lineage/idempotency, and child Start from hash-authenticated frozen files | Named human verifies the pilot, claim/metric/effect/split/seeds/baselines and explicitly approves the contract; lock is not start approval |
| One-click Campaign launch | mocked start freezes `OBJECTIVE.md`, `manifest.json`, `SOURCE_SNAPSHOT.json` and human actor/reason; duplicate active start returns 409; reconciliation reuses the same approval/receipt/hash; admission covers resource/concurrency/wall-clock gates | Test one disposable Argus project; verify local versus target-owned remote workspace mode, exact verified release SHA/origin, target-reported backend, cutoff and budget before approving a real run |
| Local/remote Argus | WebAPI route, bearer-redaction, remote-HTTPS, protocol/capability persistence and false backend-override rejection tests | Test the configured host; verify firewall/TLS/auth, target role configuration and that its reported commit/protocol/snapshot match the intended release |
| Live observability | WebSocket event replay test and campaign state projection | In a disposable run, confirm PID liveness, `making_progress`, stale snapshot, evidence/artifact and summary change independently in the UI |
| Resource-aware scientific Prompt | Portfolio/Locked invariants, hash and unconfigured-resource rejection tests | Confirm detected hardware, GPU-hour/API caps, data access, wall-clock cutoff, kill criterion and baseline commits |
| Independent Viewer | separate-process test verifies different PID/fresh workdir; evidence snapshot tests enforce allowlisted bounded previews, hashes, atomic queue claim/recovery and no score for missing/tampered evidence | Configure a different provider/process, inspect cited frozen evidence and verify the Viewer cannot write Campaign artifacts |
| Outcomes and rebuttal | API tests record pseudonymous multi-reviewer outcomes, enforce export gates, freeze idempotent rebuttal Objective and create an idle follow-up Campaign | Human verifies entered scores/text/decision, authorizes Start separately and submits any rebuttal externally |
| Source radar | parser/cache/ETag/API2 and lexical-delta tests | Run rate-limit-aware refresh; inspect nearest works manually; lexical collision is triage, never novelty proof |
| Human/integrity gates | two integrity/two review Prompt invariants; due scheduler enters approval state rather than auto-submit | Named human records idea lock, budget expansion, ethics, final claims, AI disclosure, authorship and submission decisions |
| Release safety | monitor test asserts exact `git ls-remote`; registry tests cover origin+ref+SHA, atomic replace/preserved human fields/fail-closed invalid state; staging tests cover full-SHA confirmation, isolated detached checkout, unsafe-input rejection, idempotency and preserved diagnostics with fake Git | Treat Microsoft official and lbx154 preview as different origins/builds; run real tests/canary separately; approve only for new Campaigns; never migrate a live Campaign in place |
| UI quality | TypeScript/Vite production build | Desktop/mobile, keyboard focus, reduced motion, error/empty/loading states and demo banner require rendered inspection; confirm the independent Flywheel UI visibly follows Argus brand/color/type/workbench lineage without impersonating upstream runtime truth |

The present reminder acceptance covers durable reminder state, in-app events and an
`.ics` feed. It does not cover email/webhook delivery; no such sender is implemented.
For a networked deployment, release staging also requires a repository/egress allow-list
and an authorized-origin check; syntactically valid network hosts are not automatically
trusted.

## Preflight gate for a real Campaign

A Campaign is not execution-ready until every item below is recorded:

- TeamProfile reflects the executing team's current expertise, methods, data access,
  constraints, goals and policy; the ideation run freezes the intended condition hash;
- current official deadline URL or an explicitly acknowledged forecast interval;
- configured Argus connection tested successfully, with remote bearer over HTTPS;
- explicit resource profile: actual GPU count/model, GPU-hour cap, API hard cap,
  maximum parallel jobs and timezone-qualified wall-clock cutoff with explicit UTC
  offset. Its stated-offset date must not exceed the official deadline date, or the
  forecast window start for a forecast;
- every non-compute prerequisite required by the selected topic/domain: licensed data,
  real testbed/device (for example HMD), participant recruitment and IRB/ethics where
  applicable, specialized software, proof expertise or collaborators. A green
  GPU/API resource contract alone does not mean the research is execution-ready;
- Prompt preview reviewed, content hash frozen, negative/`NO_WINNER_YET` outcomes
  accepted, and external submission disabled;
- target Argus role backends configured and verified. WebAPI Start must use
  `backend=connection-default`; a local Pi/Copilot installation does not select the
  target daemon backend;
- Argus SHA pinned, or an explicit non-production exception acknowledging
  `release_pinned=false`;
- for a new candidate: inspected full SHA, explicit isolated stage, separate tests and
  canary evidence; a staged manifest alone is never an approved release;
- source snapshot date and nearest-work review recorded;
- if an operator packet is bound, ref and exact 64-hex content SHA-256 were supplied
  together, raw ref contains no secret, and byte verification/freshness-delta policy is
  accepted; otherwise the Objective requires fresh source freeze before candidates;
- human Start approval recorded as `human_approved=true`, nonblank reason and actor;
- independent Viewer adapter configured if a real score is expected.

For a shared installation, acceptance also requires an explicit trust decision. A
TeamProfile is not a tenant. Because this release has no authentication, RBAC,
tenant-scoped rows or tenant secret isolation, mutually untrusted teams must use separate
deployments/databases/runtime roots.

## Evidence that must never be inferred

- `official_confirmed` does not mean later policy, track, anonymity or AI-disclosure
  rules are unchanged.
- Forecasts, rough ideas, lexical deltas, internal Viewer scores and progress percentages
  are not official evidence of novelty, correctness, acceptance probability or Oral
  readiness.
- Seed text mentioning a particular GPU setup is not machine inventory. Runtime probing,
  a confirmed ResourcePool and an operator-approved budget remain authoritative.
- A process being alive is not scientific progress. A stale snapshot or absent evidence
  must remain visible even when the daemon PID is healthy.
- The 290 static exports are baseline seeds, five per venue, and use that venue's
  earliest planning target. They are 58 × 5, not 85 × 5, not personalized to a
  TeamProfile and not 290 × every submission round; round-specific execution must bind
  the chosen `deadline_id` and freeze a new condition/launch manifest.
- Repository SHAs, WebAPI versions and snapshot schemas are point-in-time observations,
  not permanent latest/stable truth or a Campaign pin. Different origins or SHAs are
  not the same build even when their READMEs say sync. A locally installed or absent CLI
  does not prove the backend used by a target daemon; its live snapshot is authoritative.
- A successful Locked Contract response is not evidence that Argus started or anything
  was submitted. Its manifest must say all three are false; Start and submission are
  separate human-controlled operations.
- A conditioned Objective or imported `CANDIDATES.json` is not evidence that real Argus,
  an LLM, arXiv/OpenReview/GitHub, a Viewer evaluator or an experiment actually ran.
- A training-export eligible row is not proof that training occurred or that legal,
  ethical, venue and institutional review is complete.

## Acceptance record template

Copy this block into an operator-owned record for each release:

```text
Flywheel revision / package hash:
Checked at (timezone):
scripts/check.ps1 result:
Catalog audit result:
Existing Argus checkout before/after status:
Flywheel bind address and access-control boundary:
Argus connection / reported SHA:
Resource probe and hard budgets:
Disposable Campaign ID / Argus project ID:
WebSocket and stale/progress observation:
Viewer request ID / evaluator PID / output SHA-256:
Source refresh snapshots and rate-limit state:
Human approver and unresolved exceptions:
```

Release-specific toolchain versions, source SHAs, catalog counts and automated results
belong in the operator-owned acceptance record above. They are intentionally not frozen
into this reusable product document.

Additional tests may be added after this snapshot; always use the current
`scripts/check.ps1` result as the acceptance authority. The observed Starlette/httpx
deprecation warning is non-fatal but should be removed during dependency maintenance.
