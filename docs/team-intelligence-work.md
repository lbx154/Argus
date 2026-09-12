# Team intelligence implementation and acceptance

User objective, 2026-09-12: make Argus an evidence-driven, adaptable team that a person can supervise through its Manager. Improve the architecture through actual trial use. This is an active work record, not a completion claim.

## Required outcomes

| Requirement | Evidence required for completion | Current state |
| --- | --- | --- |
| Optional advisor with a separately selected model | Manager, Planner, Engineer and Reviewer each invoke the real tool; selected model remains distinct; actual cost/cancellation/evidence and parent call are recorded | Pi native tools and Copilot scoped MCP implemented. Real Engineer→Advisor→Engineer consultation passed; remaining real role paths need acceptance |
| Manager as a persistent supervising session | Session survives process restart; answers current progress from current project evidence; ongoing team review can change direction at a safe boundary and records why | Dialogue, ask and supervision share the durable identity; fake-backend restart, foreground priority, evidence and durable decision recovery passed; live-model continuity acceptance remains |
| Useful, concise human interaction | Desktop/mobile trial recordings show what happened, what changed, evidence and next action; generic stage labels alone do not pass | Existing invite baseline captured; candidate idle entry and concrete Advisor/Manager/peer events implemented; composer Stop now reaches the actual Pi process |
| Dynamic agent-led work | Different evidence leads to different tool/team/continuation decisions; fixed control flow cannot masquerade as agent judgment | Evidence-driven continue/steer/wait applies at real role boundaries; waiting mission A does not block independent B; broader task-type acceptance remains |
| Daemon-to-daemon exchange | Two project daemons exchange a request and correlated reply across restart; peer advice cannot acquire operator authority | Durable correlated mailbox and call-bound Manager tools implemented; queued offline messages and crash recovery tested; no automatic peer startup |
| User and project learning | Explicit scope, override, revoke and one-time instruction behavior survives restart and compaction; relevant corrections reach subsequent role prompts | Global preference defaults and project override, bounded checkpoints and real prompt roots fixed; related tests passed |
| Bounded, revisable experience and vector retrieval | Real settlement/learning updates stable records; retire/merge replaces vectors; retrieval excludes superseded content; long-run active/history storage remains bounded | Canonical lifecycle committed; scoped correction tools, successful observations, current Markdown recall and optional semantic adapter are under final independent review |
| Evidence-driven self-improvement | Accepted/rejected lessons cite real outcomes; usage or similarity alone is never evidence of correctness | Correction requires revision and reason; host verification of bounded evidence files is being integrated; model interpretation remains advisory |
| User-perspective trial and coordination | Real invite login, ordinary-user UI, Manager inquiry, advisor and control flows exercised; findings drive changes; concurrent source preserved | Latest observed live 6dbfca40ba merged. Ordinary live invitation observations and isolated full control acceptance recorded; no deployment by this thread |

## Integration boundaries

Development checkout: `/data/v-boxiuli/argus-team-intelligence-20260912`, branch `feat/team-intelligence-20260912`. Base merges previous validated control/recovery work `80b227dcb` and deployed UI `63d30f91d`. No online rollout has been performed in this goal turn.

- Advisor: new `advisor` service/tool modules, controlled runner hook and role tool declarations. Advisor suggestions do not directly mutate operator instructions or task state.
- Manager: persistent identity, current evidence context and event/phase-driven supervision. Its decision applies through existing task controls, including cancellation generations.
- Memory: canonical sources remain distinguishable; derived indexes can be replaced. Start from the actual FailureExperienceStore write/read paths, then fix profile scope and durable peer messages.
- Root agent: integration, trial observations, user-facing API/UI and hosted model selection; keep independent implementation tests before integration checks.

The other checkout `/data/v-boxiuli/argus-observable-research-20260912` committed structured-output work as `d4418c5db` and shared explanation SSE as `6dbfca40ba`; both are merged. Its latest observed release source is `/data/v-boxiuli/argus-unified-reuse-6dbfca40ba-20260912`. Recheck live source before publishing; the earlier rollout baseline is obsolete. The shared coordination note does not establish that the other session has acknowledged it.

The focused control contract and actual browser acceptance are documented in
`docs/web-control-responsiveness.md`. Old and new writers must not mix when
deploying the revised Backlog and OperatorContext storage protocols. Do not use
automatic old-image rollback after the new writer has migrated project state.

## Validation policy

Use deterministic fake backends for error, crash, cancellation, deduplication and scope tests. Use real trial interactions to establish actual user experience and actual tool/model selection; a mocked runner is insufficient evidence for those claims. Keep real trial state and private credentials out of repository fixtures and public reports.

Do not mark the overall goal complete while any requirement above remains unimplemented or supported only by indirect evidence. Updating this table is bookkeeping; code changes, test results and actual trial observations establish progress.
