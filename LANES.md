# LANES — who changes what (product-grade refactor coordination)

> Created 2026-06-26 for the multi-collaborator product-grade refactor (re-audit
> `w504b18ji`). Goal: stop collaborators stepping on each other. **Proposed by the
> autonomous-loop (Claude); operator (lbx) confirms the HAPI assignments.**
> Edit freely as ownership shifts.

## Collaborators
- **lbx** — operator. Owns product-identity decisions + reviewer contract. Commits land under `lbx154` (also the identity the Claude autonomous loop commits through).
- **HAPI** (`nssmd` / "Your Name") — actively refactoring the skill/agent backbone.
- **claude** — the autonomous overnight loop (cron `6d593683`). Stays strictly in its lane below.

## Lanes (by subsystem, since files are shared)

| Lane | Subsystems / dirs | Owner |
|---|---|---|
| **L-hapi** | `skills/`, `manager/`, `reviewer/`, the per-role **backend/runner** wiring (`apps/_runtime.py` `_role_backend`, `agent_cli/`, `adapters/`, the `author_model` plumbing) | HAPI |
| **L-claude** | `meta/`, `verticals/`, `daemon/` (drain + handoff, NOT the role-config), `life/supervisor/_core.py` (structure), `life/` observability (`event_log/notify/telemetry/telegram_bot/activity_log/stage_budget`), `planner/planner.py` (EMNLP/restart, NOT backend config), `apps/cli/` (flags), `tools/subagent/` + `team/`, `core/knobs` (new), `deploy/`, `docs/`, `tools/new_auto_research_project.py` | claude loop |
| **L-operator** | product-identity **semantics** (default vertical / paper-vs-benchmark defaults), any reviewer-contract / schema change | lbx (decision) |
| **OFF-LIMITS** | `argus_skill/islands/`, top-level `argus/` | other collaborators — **nobody in this effort touches** |

## Remaining roadmap items → lane + owner

| # | Item | Files | Lane | Status |
|---|---|---|---|---|
| 1 | flip default identity (DEFAULT_VERTICAL / paper_mission / full_emnlp_gate defaults) | `skills/vertical_select.py`, `life/supervisor/_config.py`, `verticals/_base.py` | **L-operator** (semantics) — touches L-hapi `skills/` | ⏸ awaiting lbx's call on what "unspecified" means |
| 2 | `--vertical` front-door flag | `tools/new_auto_research_project.py`, `apps/cli/` | **L-claude** (mechanics) — default value gated by #1 | partial (docs done) |
| 3 | strip EMNLP from core | `life/supervisor/_core.py`, `planner/planner.py`, `loop.py` **(L-claude)** + `reviewer/_core.py` **(L-hapi)** | **split** | core=claude; reviewer part=coordinate w/ HAPI |
| 4 | delete dead-threaded `author_model` plumbing | `apps/_runtime.py`, `daemon/life_worker.py` (config), `apps/cli/_core.py`, `team/teammate_entry.py` | **L-hapi** | their per-role-backend plumbing — HAPI cleans/confirms |
| 6 | collapse delegation frameworks | `tools/subagent/`, `team/` | **L-claude** | verify `skills/run_contract` (L-hapi) usage before any cut |
| 7 | delete dead blue/green handoff | `daemon/life_worker.py` (handoff), `planner/planner.py`, `life/research_profile.py` | **L-claude** | safe (dead code) — claude loop |
| 9 | ARGUS_* knob registry + `--config-help` | `core/knobs.py` (new), `apps/cli/` | **L-claude** | must also register HAPI's per-role `*_BACKEND/_RUNNER_BIN` knobs |
| 10 | split LifeSupervisor god-object | `life/supervisor/_core.py` | **L-claude** | coordinate: holds lbx's external-blocker latch + HAPI's role-config |
| 11 | relocate operator_sim to dev-only | `life/operator_sim.py`, `apps/_runtime.py` (import) | **L-claude** | the `_runtime` import site is L-hapi-adjacent — touch minimally |
| 12 | delete ghost dirs | `scientist/ missions/ codex_autoloop/ apps/_life_repl/` (untracked .pyc shells) | **L-claude** | trivial; `islands/ argus/` stay OFF-LIMITS |
| 14 | observability → one event bus | `life/event_log/notify/telemetry/telegram_bot/...` | **L-claude** | pure L-claude |

Done: #1-drain, #5-meta-attribution, #8-proxy-gate-visibility, #13-systemd-unit.

## Rules
1. Work only in your lane. For **split**/**coordinate** items, touch only your side; rebase before each push.
2. The Claude loop does L-claude items only. It NEVER does L-operator (waits for lbx) or L-hapi (waits for HAPI), and NEVER touches OFF-LIMITS.
3. Shared files (`daemon/life_worker.py`, `life/supervisor/_config.py`, `planner/planner.py`, `apps/cli/`): edit only your responsibility within them; expect rebases.
4. Reviewer contract / schema and product-identity defaults change ONLY with lbx's sign-off.
