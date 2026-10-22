# Docs that disagree with the code (RL-4, 2026-10-22)

Checked against `origin/dev` at `7d61e84e`. Scope: `README.md`, `README.zh-CN.md`,
`CONTRIBUTING.md` and every top-level `docs/*.md` except the roadmaps and
`RESEARCH_AGENCY_AND_VERIFICATION_TODO.md` (edited by a parallel task; it already
says it is a backlog, not a running contract).

## How it was checked

1. A script pulled every concrete reference out of the docs and checked that it
   still exists: repository paths and relative links, `argus.*` module names,
   `python -m` targets, `argus` CLI flags (against the parser built by
   `argus/apps/cli/_parser.py`), and `ARGUS_*` environment variables (against
   `argus/core/knobs.py` and every direct read in Python, TypeScript, Rust and
   the desktop scripts). It also grepped for what the October 19–22 removals
   took out: `argus.tools.subagent` and durable `direct`/`supervised` runs
   (`b88b67b6`), Agent Teams (`62dea9ac`), the `paused_*` statuses (`9f787bc7`),
   `AUTONOMY_MODE` and the `--operator-wait-exit` timer (`f0b107e2`), per-role
   backends (`84debba6`), the eight learning switches (`21a1be81`), the second
   reading (`448d829f`), the persistent Copilot ACP session (`9011e8c0`),
   session rotation (`a2830605`), `reviewer/_parsing.py` (`b608655c`), the
   footers replaced by the handoff line (`ff151a2c`), the math and kernel
   verticals moved to the Vertical Store (`d23658c7`), and sha256 (`1b2768ee`,
   `82f64e4f`).
2. Each doc was then read against the code for the claims a script cannot
   check (what a feature does, which roles exist, defaults).

`--no-operator` is still a live flag (`ARGUS_SKILL_OPERATOR_AVAILABLE=false` for
one run); `--operator-wait-exit` is still accepted and does nothing. No doc in
scope still described `AUTONOMY_MODE` or the wait-exit timer.

Actions: **fixed** means the wrong sentence was corrected in place; **banner**
means a note under the title says the doc is outdated or a historical record and
links to the current doc, without rewriting the body.

## Current guidance

| Doc | Inconsistency | Action |
|---|---|---|
| README.md, README.zh-CN.md | Version banner said v0.1.6; `pyproject.toml` is 0.1.8 | fixed |
| README.md, README.zh-CN.md | Role table: the Reviewer "runs read-only"; since `9809ca4a` it runs with the Engineer's access and files it changes are recorded as `changed_during_review` | fixed |
| README.md, README.zh-CN.md | "Credentials, payment, irreversible actions and publication always stop for a human" contradicted the README's own operator section: with `ARGUS_SKILL_OPERATOR_AVAILABLE` off, the Manager decides them in the operator's place | fixed |
| README.md, README.zh-CN.md | "Seven built-in verticals, seventeen more" (twice); four are built in and the README's own Verticals section lists twenty in the store | fixed |
| README.md, README.zh-CN.md, docs/agent-install.md | Windows "native durable subagents" with a detached worker, direct/supervised long commands and a registry; deleted in `b88b67b6` | fixed: now describes the per-turn Job Object and links windows-process-ownership.md; `tests/test_install_documentation.py` pinned the old phrase and now checks the new one |
| CONTRIBUTING.md | none found (npm scripts, CI branches, doctor code, `ARGUS_PYTHON`, archive tags match) | — |
| docs/FEATURES.md | SELF learning review after five turns, its `self.learning.review.*` events and `skills/self/` (removed); "at most one mission at a time" (default `--mission-width` is 2); built-in math/literary/hardware verticals; long commands in durable `direct` mode with Supervisor calls | fixed |
| docs/best-practices.md | `ARGUS_SKILL_SUPERVISOR_MODEL` and the Supervisor reasoning effort (removed with the subagent); "seven verticals ship, including math" | fixed: roles now match `KNOBS` (OBSERVER; MANAGER/PLANNER/ENGINEER/REVIEWER/SELF effort) |
| docs/daemon-lifecycle.md | Last paragraph described durable and direct runs | fixed: the 30-minute stall report now described for an Engineer turn (`ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS`) |
| docs/harbor.md | Launch command lacked `--no-operator`, which `argus/integrations/harbor.py` passes | fixed |
| docs/local-pr-regression-gate.md | Report `policy` and `evidence` described content hashes; `82f64e4f` replaced them with byte sizes | fixed |
| docs/LAYOUT.md | agent_cli's "warm `copilot --acp` client"; tools list named the deleted Lean check and event-log query; reviewer "pure verdict parsers, editable review file" | fixed (layering test still passes) |
| docs/CORE_CONCEPTS.md | Sessions rotate on turn/token signals; "paused or research-incomplete states" | fixed: no rotation, `parked` |
| docs/ARGUS_MEMORY.md | Correction receipt recorded source-byte hashes; "revision/digest reconciliation" | fixed |
| docs/PROJECTS.md | "No automatic cross-project Wiki route"; recall now reads earlier projects' Wikis and Skills (`b92d6579`) | fixed |
| docs/workbench-plugins.md | Per-role execution backends | fixed: one backend for every role |
| docs/how-argus-speaks.md | `PLAN_SIGNAL=` listed as a parsed token; nothing reads it | fixed: the handoff line |
| docs/model-decision-boundary.md | Engineer footer keys and Reviewer STATUS/authority-impact carriers | fixed: Engineer handoff line, Reviewer `submit_review` |
| docs/runtime-maintainability.md | Early exit on "external-work waits"; external-work heartbeat | fixed: park; registry deletion noted |
| docs/research-timeline.md | Module "in the research vertical" (it is `argus/core/timeline*.py`); a `research-timeline` Skill that does not exist; entry under "More → Workbench" | fixed. The entry path is the least certain fix: only the Workbench label was confirmed in `i18n.tsx` |
| docs/web-control-responsiveness.md | Workbench modules "Execution, AI IDE"; the UI calls them Work progress and Code workspace | fixed |
| docs/counterexample-lab.md, .zh-CN.md | Workbench page removed in `948cbb4d`; only the API projection remains; update button moved | banner + fixed path |
| docs/failure-modes-and-fixes.md, .zh-CN.md | Link to deleted `venue_research.py`; NSL novelty gate "still in the tree"; kernel_engineering spoken of as in this repo; quoted Skill text no longer in the shipped Skills | fixed the first three; banner for the quoted Skills |
| docs/paper-narrative-quality-plan.md | `academic_language_review.py` and its `--write` CLI, `draft_outline.py`, `argument_organization.py`, `reviewer_simulation.py` deleted | banner (names what still matches) |
| docs/role-handoff-design.md, .zh-CN.md | Status said "proposal"; HO-16 has landed (handoff line, one park status, `_parsing.py` and `round_checkpoint.py` deleted) | fixed the status line; body left as the proposal's record |
| docs/PRINCIPLES.md, .zh-CN.md | Policy list said "minimal footers" | fixed: "prose handoffs ending in one handoff line" |

No inconsistency found in: ROLE_SESSIONS_AND_SKILLS, backend-providers,
advisor-backend-tools, continuous-learning, recall-embeddings, building-a-vertical,
vertical-store, workflow-profiles, typescript-migration, goal-contract-authority,
acceptance-dependencies, settlement-experience-recovery, getting-started, mobile,
plugin, desktop-trial, stable-web-trial, trial-gateway, hosted-research-trial,
windows-desktop, windows-process-ownership, research-map, browser-pairing-links,
web-attachment-uploads, application-branding, operator-inbox-delivery,
exploration-without-local-hill-climbing (both languages), agent-session-titles-20260915,
single-agent-vertical-20260915.

## Point-in-time plans and audits

| Doc | Inconsistency | Action |
|---|---|---|
| docs/system-audit.md, .zh-CN.md | Measured files no longer exist (`skills/capability_trace.py`, physics gates, `daemon/self_maintenance.py`, quant/literature ledgers); "remediation" still describes footer lines | banner (historical snapshot) |
| docs/simplification-plan.md, .zh-CN.md | The files it plans to delete are already gone | banner (historical snapshot) |
| docs/architecture-simplification-plan.md | "A few trailing fields" are now the handoff line; the vertical split has happened | banner (historical snapshot) |

## Dated notes that read like current guidance

| Doc | Example of what changed | Action |
|---|---|---|
| docs/HANDOFF-2026-09-05-NEXT.md | `ARGUS_SKILL_UNPRICED_COST_POLICY` removed | banner (historical record) |
| docs/handoff-2026-09-04-capability-tests.md | Session rotation and `ROLE_SESSION_MAX_INPUT_TOKENS` removed; `argus_skill.tools.subagent` gone | banner |
| docs/notes-2026-09-06-voice-and-autonomy.md | Second reading removed; `argus_skill/*` paths moved to `argus/` | banner |
| docs/token-consumption-fixes-2026-09-06.md | Persistent Copilot ACP session removed | banner |
| docs/experiment-capability-2026-09-16.md | REFLECTION, ANSWER_LEARNING, CONSOLIDATE_INTERVAL_S, RECALL_SIBLING_WIKIS folded into `ARGUS_SKILL_LEARNING`; `FIGURE_MODEL` removed | banner |
| docs/team-intelligence-work.md | Calls itself the active work record; `argus_skill/advisor` and `messaging` moved; Agent Teams removed | banner |
| docs/team-intelligence-history-20260912.md | Session rotation and client-side budgets removed | banner |
| docs/web-product-acceptance-20260915.md | Site-wide daily token cap and in-run budget monitor removed | banner |
| docs/WHAT_ARGUS_GREW.md | Its runtime-code table points at `argus_skill/...` and `verticals/math/*` | banner |
| docs/team-intelligence-checkpoint-0e2-to-700-20260913.md | already marked historical | — |
| docs/CURRENT_ROLE_PROMPTS.md | already marked outdated (the translation still asks for a key-value footer) | — |

## Script hits that are not errors

- `docs/LAYOUT.md` "Planned moves" names target paths that do not exist yet by design.
- `scripts/build_catalog.py` (building-a-vertical, vertical-store) and
  `argus_verticals/...` (exploration docs) live in the argus-verticals repository, as the docs say.
- `argus/_contracts` is generated at wheel build; `argus/api` and `argus/runtime`
  in typescript-migration are npm package names.
- Build output (`desktop-tauri/src-tauri/target/...`, `desktop-tauri/build/previews/`,
  `.keys/`) and user-chosen names (`ARGUS_TRIAL_ROOT`, `ARGUS_RECALL_EMBEDDING_API_KEY`)
  are not expected in the tree; `--provider` in trial-gateway is a `pi` flag.

## Left for a decision

- docs/agent-install.md says worktree-isolated maintenance workers read the
  account's credential files read-only; not confirmed either way.
- docs/FEATURES.md's scenario table still records "maximum one running mission"
  as the outcome of a scenario that was run; left as a record.
- Outside the docs: `argus/integrations/harbor.py` sets `ARGUS_SKILL_PLANNER_MODEL`,
  which nothing reads (the knob is `ARGUS_SKILL_PLAN_MODEL`).
