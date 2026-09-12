# Team intelligence implementation and acceptance

User objective, 2026-09-12: make Argus an evidence-driven, adaptable team that a person can supervise through its Manager. Improve the architecture through actual trial use. This is an active work record, not a completion claim.

## Required outcomes

| Requirement | Evidence required for completion | Current state |
| --- | --- | --- |
| Optional advisor with a separately selected model | Manager, Planner, Engineer and Reviewer each invoke the real tool; selected model remains distinct; actual cost/cancellation/evidence and parent call are recorded | Pi native tools and Copilot scoped MCP implemented. Real Engineer and Manager complete turns passed. Planner and Reviewer completed real consultations; their final main replies were rejected before upstream dispatch by trial quota reservation, so complete-turn acceptance remains partial |
| Manager as a persistent supervising session | Session survives process restart; answers current progress from current project evidence; ongoing team review can change direction at a safe boundary and records why | Dialogue, ask and supervision share the durable identity. Real Manager follow-up in a new Python process retained the provider thread and prior context while reading updated evidence. Foreground priority and durable decision recovery passed deterministic checks |
| Useful, concise human interaction | Desktop/mobile trial recordings show what happened, what changed, evidence and next action; generic stage labels alone do not pass | Existing invite baseline captured; candidate idle entry and concrete Advisor/Manager/peer events implemented; composer Stop now reaches the actual Pi process |
| Dynamic agent-led work | Different evidence leads to different tool/team/continuation decisions; fixed control flow cannot masquerade as agent judgment | Evidence-driven continue/steer/wait applies at real role boundaries; waiting mission A does not block independent B; broader task-type acceptance remains |
| Daemon-to-daemon exchange | Two project daemons exchange a request and correlated reply across restart; peer advice cannot acquire operator authority | Durable correlated mailbox and call-bound Manager tools implemented; queued offline messages and crash recovery tested; no automatic peer startup |
| User and project learning | Explicit scope, override, revoke and one-time instruction behavior survives restart and compaction; relevant corrections reach subsequent role prompts | Global preference defaults and project override, bounded checkpoints and real prompt roots fixed; related tests passed |
| Bounded, revisable experience and vector retrieval | Real settlement/learning updates stable records; retire/merge replaces vectors; retrieval excludes superseded content; long-run active/history storage remains bounded | Canonical lifecycle, scoped correction tools, successful observations, current Markdown recall and bounded optional HTTP adapter committed and independently checked. Default retrieval uses lexical hashing. The one real embedding attempt returned HTTP 400; real semantic retrieval is not established |
| Evidence-driven self-improvement | Accepted/rejected lessons cite real outcomes; usage or similarity alone is never evidence of correctness | Correction requires revision and reason. Host reads bounded scoped evidence bytes and records their hashes; symlink/FIFO isolation and concurrent index updates are tested. A byte hash does not establish that the model's interpretation is true |
| User-perspective trial and coordination | Real invite login, ordinary-user UI, Manager inquiry, advisor and control flows exercised; findings drive changes; concurrent source preserved | Latest observed live b084d44f93 merged. Ordinary live invitation observations and isolated full control acceptance recorded; no deployment by this thread |

## Integration boundaries

Development checkout: `/data/v-boxiuli/argus-team-intelligence-20260912`, branch `feat/team-intelligence-20260912`. Base merges previous validated control/recovery work `80b227dcb` and deployed UI `63d30f91d`. No online rollout has been performed in this goal turn.

- Advisor: new `advisor` service/tool modules, controlled runner hook and role tool declarations. Advisor suggestions do not directly mutate operator instructions or task state.
- Manager: persistent identity, current evidence context and event/phase-driven supervision. Its decision applies through existing task controls, including cancellation generations.
- Memory: canonical sources remain distinguishable; derived indexes can be replaced. Start from the actual FailureExperienceStore write/read paths, then fix profile scope and durable peer messages.
- Root agent: integration, trial observations, user-facing API/UI and hosted model selection; keep independent implementation tests before integration checks.

The other checkout `/data/v-boxiuli/argus-observable-research-20260912` committed structured-output work as `d4418c5db`, shared explanation SSE as `6dbfca40ba`, terminal HTTP observation as `c7b8851b8`, and reader continuity/related evidence as `177c4b4fc` / `b084d44f93`; all are merged. Its latest observed release source is `/data/v-boxiuli/argus-unified-reuse-b084d44f93-20260912`. Recheck live source before publishing; earlier rollout baselines are obsolete. The shared coordination note does not establish that the other session has acknowledged it.

The focused control contract and actual browser acceptance are documented in
`docs/web-control-responsiveness.md`. Old and new writers must not mix when
deploying the revised Backlog and OperatorContext storage protocols. Do not use
automatic old-image rollback after the new writer has migrated project state.

## Integrated validation and limits

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

The real Engineer consultation used 3 requests / 3,708 metered tokens. Further
Manager, Planner and Reviewer acceptance used 8 upstream requests / 14,853
metered tokens without retries. The Planner/Reviewer final reply failures were
`trial_quota_exceeded` reservation rejections; positive remaining actual-token
balances do not establish that the refused reservation would fit. We did not
repeat paid calls to obtain a passing sample. Readonly Advisor support for other
backends is described in `docs/advisor-backend-tools.md`.

One semantic-adapter request returned HTTP 400 and no vector. Default lexical
fallback remained available. The captured provider catalog alone does not prove
that the current embedding endpoint/authentication works, and this attempt does
not establish that the model is globally unsupported.

The local candidate image for `7c84cc09d` passed both actual Uvicorn/UDS and
comprehensive offline API/tool/state checks without live mounts or provider
calls. It predates the b084 merge and is superseded by a newly built candidate
before release. The separately retained TUI artifact is explicitly versioned
`e3dc51bfd64c1513a1cb24467ae6e7428cf2a6aa`.

Release must include the meter/gateway source and explicit model catalogs as
well as tenant backends, native instance and portal/frontend. The observed live
gateway and all 11 Pi registries still allow only `gpt-5.5`; independent Advisor
selection requires the new routing code and the same host-approved catalog on
both sides. Advisor remains opt-in. Shared metering cannot be reset or rolled
back casually during project-state recovery.

## Validation policy

Use deterministic fake backends for error, crash, cancellation, deduplication and scope tests. Use real trial interactions to establish actual user experience and actual tool/model selection; a mocked runner is insufficient evidence for those claims. Keep real trial state and private credentials out of repository fixtures and public reports.

Do not mark the overall goal complete while any requirement above remains unimplemented or supported only by indirect evidence. Updating this table is bookkeeping; code changes, test results and actual trial observations establish progress.
