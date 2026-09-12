# Team intelligence implementation and acceptance

User objective, 2026-09-12: make Argus an evidence-driven, adaptable team that a person can supervise through its Manager. Improve the architecture through actual trial use. This is an active work record, not a completion claim.

Current status: bounded real Copilot semantic recall has passed acceptance.
Dynamic Engineer/Planner memory, complete observation identity, readable
incomplete-observation feedback and Manager handoff fixes are committed in
`4092a393d` with focused offline coverage. The previously observed live change
`63738a4a8` is merged. The complete read-only live capture at 23:02:54 UTC observed
clean source `98daa0d01a3b5d53297edee848df8aee4153e448`, all 11 APIs ready and stable
identities throughout capture; that source is merged in `6f05afb10`. The merged frontend passed 937 tests and its standard generated-contract/type/build checks.
Frozen `ec91af13e` then passed the complete Python suite: 10,045 passed,
56 skipped and no failures. Its image/API/UDS, byte comparisons and browser
control checks passed. The browser inspection found a public-objective event
leak, and a delayed local embedding fixture found a cancellation delay; both
follow-ups are tracked below and require separate final-candidate validation.
Manager's four-phase real-model acceptance remains partial. This thread has
not deployed these follow-ups.

## Required outcomes

| Requirement | Evidence required for completion | Current state |
| --- | --- | --- |
| Optional advisor with a separately selected model | Manager, Planner, Engineer and Reviewer each invoke the real tool; selected model remains distinct; actual cost/cancellation/evidence and parent call are recorded | Pi native tools and Copilot scoped MCP implemented. Real Engineer and Manager complete turns passed. Planner and Reviewer completed real consultations; their final main replies were rejected before upstream dispatch by trial quota reservation, so complete-turn acceptance remains partial |
| Manager as a persistent supervising session | Session survives process restart; answers current progress from current project evidence; ongoing team review can change direction at a safe boundary and records why | Real persistence, STEER and WAIT reached role boundaries; the four-phase model acceptance remains partial. Complete observation identity, safe incomplete-source handling and evidence-bearing session handoffs have offline coverage. Provider-history capacity remains unresolved |
| Useful, concise human interaction | Desktop/mobile trial recordings show what happened, what changed, evidence and next action; generic stage labels alone do not pass | Existing invite baseline captured; candidate idle entry and concrete Advisor/Manager/peer events implemented; composer Stop now reaches the actual Pi process |
| Dynamic agent-led work | Different evidence leads to different tool/team/continuation decisions; fixed control flow cannot masquerade as agent judgment | Evidence-driven continue/steer/wait applies at real role boundaries; waiting mission A does not block independent B; broader task-type acceptance remains |
| Daemon-to-daemon exchange | Two project daemons exchange a request and correlated reply across restart; peer advice cannot acquire operator authority | Durable correlated mailbox and call-bound Manager tools implemented; queued offline messages and crash recovery tested; no automatic peer startup |
| User and project learning | Explicit scope, override, revoke and one-time instruction behavior survives restart and compaction; relevant corrections reach subsequent role prompts | Global/project precedence and bounded checkpoints are implemented. Real runtime prompt assembly now refreshes Engineer memory each round and Planner memory at its call boundary; offline regressions cover correction/retraction and preference revocation. Legacy callers retain snapshots; the settlement-to-capture window remains |
| Bounded, revisable experience and vector retrieval | Real settlement/learning updates stable records; retire/merge replaces vectors; retrieval excludes superseded content; long-run active/history storage remains bounded | Canonical lifecycle and bounded optional adapter are implemented. Real Copilot semantic recall passed 13 checks using 4 requests / 128 actual tokens, including restart cache reuse, revision and retraction. Default recall remains lexical hashing; this bounded probe does not establish long-run learning quality |
| Evidence-driven self-improvement | Accepted/rejected lessons cite real outcomes; usage or similarity alone is never evidence of correctness | Correction requires revision and reason. Host reads bounded scoped evidence bytes and records their hashes; symlink/FIFO isolation and concurrent index updates are tested. A byte hash does not establish that the model's interpretation is true |
| User-perspective trial and coordination | Real invite login, ordinary-user UI, Manager inquiry, advisor and control flows exercised; findings drive changes; concurrent source preserved | Live `63738a4a8` is merged; clean `98daa0d01a3b5d53297edee848df8aee4153e448` and all 11 ready APIs were observed at 23:02:54 UTC, with stable capture identities. Its integration passed 937 frontend tests and build. The cancelled-triage late-reply race is repaired with 117 focused backend tests; combined frozen validation follows. No deployment by this thread |

## Integration boundaries

Development checkout: `/data/v-boxiuli/argus-team-intelligence-20260912`, branch `feat/team-intelligence-20260912`. Candidate `4092a393d` includes the previous control/recovery work, observed live changes through `63738a4a8` (merge `d6a7ce231`), and the current runtime/Manager follow-ups. Newly observed `98daa0d01a3b5d53297edee848df8aee4153e448` is merged in `6f05afb10`. The combined source requires its own frozen validation. No online rollout has been performed by this thread.

- Advisor: new `advisor` service/tool modules, controlled runner hook and role tool declarations. Advisor suggestions do not directly mutate operator instructions or task state.
- Manager: persistent identity, current evidence context and event/phase-driven supervision. Its decision applies through existing task controls, including cancellation generations.
- Memory: canonical sources remain distinguishable; derived indexes can be replaced. Start from the actual FailureExperienceStore write/read paths, then fix profile scope and durable peer messages.
- Root agent: integration, trial observations, user-facing API/UI and hosted model selection; keep independent implementation tests before integration checks.

The other checkout `/data/v-boxiuli/argus-observable-research-20260912` committed structured-output work as `d4418c5db`, shared explanation SSE as `6dbfca40ba`, terminal HTTP observation as `c7b8851b8`, reader continuity/related evidence as `177c4b4fc` / `b084d44f93`, source-first lesson preview as `26886021c5`, and task-attached pending decisions as `63738a4a8`; all are merged. The retained `26886021c5` release observation is historical. The newer complete read-only receipt is `rollout-98daa-observation-20260912T230254Z.json`: source `98daa` was clean, all 11 APIs were ready, and identities remained stable during capture. This establishes that observed baseline, not deployment or user-flow acceptance of the new candidate. Recheck for subsequent drift before publishing. The shared coordination note does not establish that the other session has acknowledged it.

The focused control contract and actual browser acceptance are documented in
`docs/web-control-responsiveness.md`. Old and new writers must not mix when
deploying the revised Backlog and OperatorContext storage protocols. Do not use
automatic old-image rollback after the new writer has migrated project state.

## Integrated validation and limits

The complete runs below certify their named immutable baselines. They do not
certify the subsequent runtime/Manager follow-ups or the in-progress `98daa`
merge; the next combined immutable run remains pending.

The implementation was committed as `b42251b7e`; `48def20fc` then declared
the composed Manager/supervisor type contracts. Against the c7 live baseline,
the type comparison introduced no diagnostics and removed 151. The remaining
1,323 diagnostics are inherited; this is not a clean whole-repository mypy run.

The first immutable full Python run found nine failures: outdated theme-hook,
event-writer injection, direct/staged math expectations, effective-prompt logging
and Copilot test-gateway signatures. `49d82443f` repaired those test contracts;
the focused real Copilot/local-gateway and delivery/math checks passed. The
original failed-run evidence is retained. A complete rerun uses immutable
`7c84cc09d`, before the final b084 live merge.

That rerun completed with 9,924 passed, 56 skipped and one failure. The remaining
failure was the Python MCP test client closing its receive stream before
consuming the server's cancellation response. `04c9cf5f6` now awaits that
response and verifies the session still answers a ping; all 20 related tests
and 20 separately parametrized cancellation/cleanup repetitions passed. This
changes the test lifecycle, not production cancellation behavior.

The final complete baseline on immutable `e4aa47e68` passed 9,942 tests with
56 skips and no failures/errors. All 9,998 collected tests ran exactly once,
grouped by file in four processes with independent short temporary roots.
An earlier shard harness used paths too long for Unix sockets and exposed a
readonly file fixture outside its probe directory; that invalid run is retained
and marked terminated. The fixture now uses the actual readonly worker with an
explicit workspace and isolated cache. After the subsequent 268 live merge,
337 affected backend/control checks and all 879 frontend tests passed, with
standard type/build checks. These follow-up checks are distinct from the
complete baseline; unchanged suites were not rerun for unrelated UI changes.

After merging b084, all 296 map/control backend checks and all 856 frontend
tests passed; the standard frontend build includes TypeScript and generated
contract checks. The merged changes do not alter request cancellation or model
dispatch. Final artifact and image identities are tracked in the private release
manifest rather than inferred from a moving working tree.

Independent review also reproduced a b084 related-source cache gap: changing
or deleting neighboring task B could leave task A's explanation cached with
B's old assignment. The follow-up checks the bounded neighboring fields
actually supplied to the models, preserving the original source snapshot.
Filtered task views ask the backend to validate an open reader once per source
cursor when its neighbors are hidden; unchanged sources reuse the cache without
a model call. Shared in-flight requests cannot certify cards they did not
submit. This does not change task state or cancel independent map generation.

`0faeddbb9` extends the source check to the shared research reader. Normal and
preview requests have distinct fixed mode fields, so an unfinished preview
cannot block normal reading. Receipts bind the captured source cursor, card,
model settings and input; cached success cannot swallow a later source check.
Coalesced or unavailable replies retain the old explanation as pending and
preserve the existing explicit retry action. Readonly views do not generate.

The real Engineer consultation used 3 requests / 3,708 metered tokens. Further
Manager, Planner and Reviewer acceptance used 8 upstream requests / 14,853
metered tokens without retries. The Planner/Reviewer final reply failures were
`trial_quota_exceeded` reservation rejections; positive remaining actual-token
balances do not establish that the refused reservation would fit. We did not
repeat paid calls to obtain a passing sample. Readonly Advisor support for other
backends is described in `docs/advisor-backend-tools.md`.

A subsequent bounded supervision run used the real Pi/Manager path for two
evidence conditions. Manager directed repair of a pooled-mean calculation and
waited only for the task with an unresolved operator-owned unit question; the
resulting directive and WAIT reached actual role boundaries. Both judgments
and citations passed independent review, using 2 requests / 4,066 actual tokens.
The corrected-evidence follow-up required a 22,635-token reservation against
15,934 remaining, so admission stopped before another provider request. The
four-phase model acceptance remains partial; it was not repeated for a pass.

That run exposed a reporting defect: supervision flattened quota refusal into
an evidence/control failure, and the web feed hid all causes behind an unapplied
adjustment message. Receipts/events now retain the failure stage, canonical stop
kind, known error code, call ID and exit status. The web feed distinguishes quota,
timeout, cancellation, invalid decisions and interrupted application without
displaying raw provider bodies. Existing expiry, ownership and recovery rules
remain unchanged; the feedback fix is covered with local failures, not another
paid model run.

The observation follow-up separates complete canonical evidence identity from
bounded presentation. Task inventory and source semantics are hashed before
excerpting, so a change after the old 1,600-character cutoff invalidates a cached
judgment and fences a late result. A single rendered observation has a 16 KiB
UTF-8 budget: objective, acceptance and pending-question fields are preserved
before secondary descriptions. If required facts cannot fit, the receipt names
the unobserved fields and no new model judgment is requested. Source reads remain
bounded to 128 KiB; oversized, invalid, changing, FIFO and symlink sources are
explicitly unobserved rather than treated as complete empty evidence. Incomplete
sources retain bounded-prefix/filesystem change signatures. The optional
`observation_incomplete` code lets the web feed say that required project
evidence could not be fully read, without claiming that the model returned an
invalid decision. Existing failed/superseded status and ownership fences remain.

Runtime learning now reaches the actual Engineer prompt in direct and staged
work, including rolling sessions. Runners declaring the new capability omit
mutable recall and OperatorContext from immutable mission text, read current
memory each round, and retain their separate live operator projection. Planner
uses an independent shared-context provider immediately before drafting; it does
not receive Engineer-only runtime, repair or OperatorContext content. A failed
refresh reports unavailable memory rather than restoring the earlier snapshot.
Offline integration checks exercise real settlement, correction, retraction,
global/project preference precedence and later role prompts. Legacy runners
without the explicit provider capability, and other unchanged snapshot callers,
retain their previous behavior; this is not a guarantee for every custom caller.
The interval between durable mission settlement and experience capture,
including stop/crash paths, still lacks an accepted atomic capture/recovery
guarantee.

Manager session handoffs now retain bounded goal, trigger, evidence-revision and
task excerpts rather than four copies of the static supervision instructions.
Redaction occurs before the existing 2,000-character request and 3,000-character
answer limits; only four turns are retained. These are continuity excerpts,
not a replacement for current canonical evidence. Normal calls still resume the
same provider thread, and no new automatic rotation policy was added.

These limits do not bound the accumulated provider conversation. Current prompt
refresh does not erase provider history or rewrite authored checkpoints and task
text. Pi has real model-assisted compaction, but with the tested 128,000-token
window its default threshold is about 111,616 model tokens. Trial admission uses
a different conservative byte-based reservation, which can reject the resumed
history much earlier. Budget-aware early compaction/rotation and long-running
latency/capacity acceptance remain unresolved; the original 22,635 reservation
refusal and partial four-phase result remain unchanged.

The observation/runtime/architecture combination passed 94 offline checks before
the final feedback addition; subsequent observation/failure and session-handoff
checks also passed. Their logs, XML and source identities remain separate from
the earlier full baseline and the next combined freeze. A further deterministic
control probe confirmed that a cancelled triage result can reach journaling and
response delivery before its cancellation check. Its original failing receipt
is retained as `cancelled-triage-reply-before-20260912/report.json`; repair and
acceptance are still in progress.

The original semantic-adapter request returned HTTP 400. A single diagnostic
confirmed a plaintext provider rejection; Copilot's published request contract
uses an input array without `encoding_format`, and successful replies omit
`model`. The explicit Copilot format now supports that contract while retaining
the default OpenAI response identity check and existing cache keys. A bounded
production-adapter acceptance made four real requests / 128 actual tokens:
a Chinese deadlock query selected the English database experience by semantic
similarity over an unrelated newer gardening record; reopening reused cached
vectors, revision replaced the searchable vector, and retraction removed the
experience from recall. All thirteen assertions passed with synthetic content
and disposable state. Default recall remains local lexical hashing.

Two-process peer acceptance on frozen `6a2cc4380` passed 31 checks through
`LifeSupervisor.tick`, the persisted Manager, `AgentCliBackend` and call-bound
peer bridge. Offline queueing survived restart, request/reply each received an
ACK, and subsequent restarts caused no extra calls or messages. Operator/WAIT
state stayed unchanged. Inference used a local deterministic CLI substitute;
this establishes runtime delivery and recovery, not autonomous model judgment
or production service-manager behavior.

The local candidate image for `7c84cc09d` passed both actual Uvicorn/UDS and
comprehensive offline API/tool/state checks without live mounts or provider
calls. It predates the b084 merge and is superseded by a newly built candidate
before release. The separately retained TUI artifact is explicitly versioned
`e3dc51bfd64c1513a1cb24467ae6e7428cf2a6aa`.

Release must include the meter/gateway source and explicit model catalogs as
well as tenant backends, native instance and portal/frontend. An earlier
recorded gateway/registry observation allowed only `gpt-5.5` across all 11 Pi
registries. Source/API readiness in the newer `98daa` capture does not by itself
establish independent Advisor model-routing acceptance.
Independent Advisor selection requires the routing code and the same
host-approved catalog on both sides. Advisor remains opt-in. Shared metering
cannot be reset or rolled back casually during project-state recovery.

## Validation policy

Use deterministic fake backends for error, crash, cancellation, deduplication and scope tests. Use real trial interactions to establish actual user experience and actual tool/model selection; a mocked runner is insufficient evidence for those claims. Keep real trial state and private credentials out of repository fixtures and public reports.

Do not mark the overall goal complete while any requirement above remains unimplemented or supported only by indirect evidence. Updating this table is bookkeeping; code changes, test results and actual trial observations establish progress.


## Cancelled inline reply: confirmed defect and narrow fix

The deterministic before audit returned HTTP `cancelled` while still writing a
late triage response to the transcript, `ui.argus` event and streamed delta.
The response is now checked for cancellation immediately after triage returns,
before reply journaling or learning hooks. Buffered provider fragments are also
dropped after cancellation. Natural-language pause retains its own control
confirmation; it must not be mistaken for a cancelled old model request.

`cancelled-reply-fixed-focused-v3.xml` records 117 passing focused checks,
including eight HTTP/SSE cases for request cancellation and generation changes,
with both late reply and provider failure. The independent before/after audit
is retained. Earlier over-broad implementation and fixture failures are also
retained, and are not described as passing. This does not assert that all
possible concurrent cancellation/write races are linearized.

The original browser probe measured replacement-message admission only. Its
replacement is being extended to observe canonical goal/backlog updates and
queue claim separately from daemon wake requests. A fixture wake callback does
not establish a real Supervisor/Engineer start.


## Public objectives and interruptible recall

The ec91 browser screenshots showed internal `[BOUNDED TASK CONTEXT]` and
`[CURRENT OPERATOR MESSAGE]` text in the task explanation until reload.
Stored continuous and backlog goals were correct: `life.manager.intent` events
had published the model-facing routing body as their public objective. This
is a producer contract defect, not a classifier-fixture or CSS problem.
The repair gives the prepared handoff a separate public operator objective;
started/failed events use that field, completed events use the normalized
execution task, and the model still receives its full routing context. Python
and TypeScript projections prefer a historical completed event's
`execution_task` when available. The frontend follow-up passed 942 tests and
standard build/type/generated-contract checks; per-commit final evidence
remains in the private validation receipts.

With optional external embedding enabled and a cold cache, the ec91 local
HTTP fixture observed a stop-to-prelude-return delay of 6,206.8 ms and a total
stop-to-loop-abort delay of 6,215.0 ms. A second embedding HTTP request began
after stop. The main model was never invoked. The fixture used two independent
3-second waits for knowledge and experience retrieval; these measured values
must not be described as the static combined budget ceiling. Default lexical
retrieval does not use that network wait. The interruption repair must respond
to both the request scope and the Supervisor stop event, skip subsequent
requests/reservations after cancellation and retain accounting for any request
already sent. This audit made no external provider call.

The backend public-objective follow-up passed 281 focused checks. Persisted
Mission View cursors now carry an internal `projection_revision`; a prior
cursor is rebuilt through the existing bounded replay path, including rotated
logs. This fixes the otherwise unchanged-file fast path that would have kept
an old polluted projection. The public schema stays at 7; missing-history
errors remain explicit, and repeated reads after reconstruction are stable.
