# Argus Core Concepts

This document defines the canonical runtime hierarchy and terminology for Argus
at commit `ae2daa1fbc2c918b4e7126151fe55eb68fd0cb98`
(`https://github.com/lbx154/Argus.git`). It complements the existing
`plugins/argus/CONTEXT.md` glossary, which can link here for this runtime model.
Chosen model: `Operator -> Projects -> Missions -> Roles -> role/provider sessions or turns`.

For current implementation entry points, state ownership, recovery boundaries,
and the staged refactoring work, see the
[runtime maintenance map](runtime-maintainability.md).

The OperatorContext storage-root clarification below was updated against main
on 2026-09-06; the original source references retain their baseline revision.

```
Operator
  -> Projects
       -> Missions
            -> Roles
                 -> role/provider sessions or turns
```

## Hierarchy and cardinality

An **Operator** is the human or external driver that asks Argus to do work. The
Operator is outside the Argus role system: Manager, Planner, Engineer, Reviewer,
and teammate are internal runtime roles, not names for the Operator. One Operator can operate many Projects.

A **Project** is the persistent unit of work. It has one execution/work directory
and one Argus-owned project state directory under
`~/.argus-skill/projects/<project-id>/`: `global_root()` defaults to
`~/.argus-skill`, and the state collection is stored on disk as `projects/`
[`argus_skill/core/paths.py:62-137`].
Current UI and code paths may call that state directory a "session" or
`life_dir`; conceptually it is project state. The runtime records a session id,
display name, objective, launch directory, and authoritative `workdir`. One Project contains many Missions.

A **Project state directory** or **`life_dir`** is the persistent Argus-owned
state root for one Project. It is not the execution root and not a role session;
it holds the durable project files that survive mission and provider-session
turnover.

A **Mission** is a bounded work or backlog item inside a Project. It carries an
objective, scope, acceptance check, context references, non-goals, dependency
edges, optional execution workdir, Manager routing evidence, pending Operator
question state, and outcome fields
[`argus_skill/life/memory.py:728-825`]. The supervisor claims one backlog item,
builds mission context, invokes the runner, derives outcome fields, applies
repair and stage guards, finalizes status, and emits the mission outcome. One Mission invokes multiple Roles over one or more turns.

A **Role** is an internal responsibility boundary. The runtime describes Manager
as the front door, Planner as the next-work queueing role, Engineer as the L1
implementation role, and Reviewer as the L2 acceptance role
[`argus_skill/core/role_config.py:14-64`].
Manager selects workflow and is the only role allowed to change project stages
[`argus_skill/builtin_skills/manager/argus-manager-role.md:8-15`]. Planner reads
current project state and delegates legal next work; it does not implement tasks
or edit project files. Engineer produces the requested artifact, code, analysis,
or experiment and hands checkable evidence to Reviewer. Reviewer independently
judges the current mission and can accept, redirect, or block with statuses such
as `done`, `continue`, `blocked`, or `replan_requested`
[`argus_skill/builtin_skills/reviewer/argus-reviewer-role.md:8-16`]. A
**teammate** is a concurrent worker role for disjoint delegated tasks.

A **role session** or **provider thread** is model/backend conversation
continuity for one role. It can be fresh, mission-scoped, or rolling, and it is
replaceable context, not project or mission authority. Role capsules persist
compact metadata such as role, policy, objective revision, workdir, backend,
model, thread id, turn count, inspected paths, and decisive output
[`argus_skill/core/role_session.py:23-155`].
Role-session prompts point back to the capsule, mission contract, latest handoff,
and frontier while warning that project artifacts remain authoritative. Rolling
sessions resume until branch, turn, token, or quality signals rotate them; a
fresh policy intentionally clears the provider thread. Each Role may use one or more provider sessions or turns over time.

## Authority and lifecycle

Durable truth remains in Argus state, not in any model transcript. A Project
persists across Missions. A Mission may settle as terminal `done`, `failed`,
`aborted`, `skipped`, or `superseded`; it may also remain in recoverable paused
or research-incomplete states, and iteration can requeue the same item for
another bounded cycle [`argus_skill/life/memory.py:653-685`,
`argus_skill/life/supervisor/_mission_execution_settlement.py:474-491`].
Those paused and research-incomplete statuses are resumable mission states.
Terminal rows are not resurrected; a new attempt after terminal failure requires
fresh mission state. Project lifecycle is separate from role/provider continuity.
Role/provider sessions may be discarded, rotated, or resumed, but the event log,
backlog, handoff files, checkpoints, and operator context remain the durable
authority.

## OperatorContext boundary

`OperatorContextStore` is instantiated with one caller-selected root and writes
`operator_context.jsonl`, `operator_context.json`, and `operator_context.lock`
under that directory. Records carry `mission`, `project`, or `global` labels, and
projections sort by scope precedence, filter by role, and bind bounded
directives to the current mission
[`argus_skill/core/operator_context.py`]. The label `global` does not replicate
a record between stores. However, the caller can select the shared global root:
`MemoryBundle.root` returns `global_mem.root`, and mission preludes use that root
when building operator context
[`argus_skill/life/memory.py`,
`argus_skill/life/supervisor/_mission_execution_runtime.py`]. Therefore the store
is not always project-local, and a shared-root directive can affect multiple
projects that consume it.

## Concept-to-storage mapping

| Concept | Current storage surface |
| --- | --- |
| Project root | The execution `workdir` recorded in session metadata and passed into daemon/runner configuration. |
| Project state directory / `life_dir` | `~/.argus-skill/projects/<project-id>/`, also called session state by code and used as the root for project-owned runtime files. |
| Backlog and mission state | `backlog.jsonl` for live mission rows plus `backlog.archive.jsonl` for terminal rows. |
| Events | `events.jsonl` and retained rollovers under `life_dir`; this is the durable replay surface for supervisor/runtime events. |
| Handoffs | `handoffs/<mission-id>/` contains `mission.json`, `CHECKPOINT.md`, `frontier.json`, `latest.json`, and round handoffs [`argus_skill/life/context_packet.py:17-344`]. |
| Role sessions | `role-sessions/<role>.json` capsules, including Planner, Engineer, Reviewer, and teammate state where a role has a durable provider context. |
| Operator context | `operator_context.jsonl` ledger plus `operator_context.json` projection and lock under the caller-selected project or shared global root. |

## Glossary

Canonical names for concepts that today carry several identifiers in code. The
"Retired names" column lists spellings that must not spread to new code; the
declared-layering section of `tests/test_architecture_invariants.py` pins the
counts of the project-state-directory spellings (first row) and of `memory.root`
so they can only go down; the other rows are conventions only. Permanent aliases
are listed last and are not retired. Source: `docs/audits/architecture-clarity-2026-09-14.md`, 4.3,
and decision card 2 for Curator.

| Concept | Canonical name | Retired names | Notes |
| --- | --- | --- | --- |
| Project state directory `~/.argus-skill/projects/<id>/` | `life_dir` (identifier); "project state directory" (prose); `core.paths.project_state_root(sid)` once phase 7 adds it | `life_root`, `memory_root`, `session_root`, `project_dir`, `manager_session_root`, `session_state_root()`, `session_states_root()` | The 944 existing `life_dir` uses stay. Today `core.paths.session_states_root()` returns `projects/`. |
| Host root `~/.argus-skill` | `global_root` (`core.paths.global_root()`) | `MemoryBundle.root` (after decision card 3), the three `_resolve_global_root` copies | `MemoryBundle.root` returns the host root while `LifeMemory.root` returns the project directory; the daemon injects a `MemoryBundle`. Phase 8 gives `.root` one meaning. |
| Execution workdir | `workdir`; `project_root` keeps this meaning only inside `VerticalContract` hooks, `verticals/` and `domains/` | `project_root` meaning a state directory inside framework packages (for example `life/memory.py`, `manager/control_state.py`), or the host root (`webapi/routes/context.py`) | One word for the directory the roles edit. |
| Backend name (`codex`, `claude`, `copilot`, ...) | `BackendName` in `core.backend_names` (phase 1) | the `Literal` also called `RunnerBackend` in `agent_cli/runner_backend.py` | `RunnerBackend` remains the name of the Protocol in `core/ports.py`; the alias is kept until phase 6. |
| Runner vs Backend | A Runner executes one mission (the `_MissionRunner` protocol in `life/supervisor/_config.py`; `_SkillLoopRunner` in `apps/_runtime.py`, public as `SkillLoopRunner` from phase 5); `SkillLoop` (`loop.py`) is the round loop the runner drives and keeps its name; a Backend implements the `RunnerBackend` port | — | `planner_runner=` receives a backend today; documented here, parameter not renamed. |
| life | `life`: one Project's continuous life across missions (memory, backlog, supervisor, operator channels), the `argus_skill/life/` package | — | Kept and defined rather than renamed: 149 test files, the `--life-dir` flag and 49 `life.*` event names depend on it. |
| pipeline | which vertical, which stage, which checklist (`PIPELINE_STATE.json`); the `pipeline/` package from phase 3 | the stage machine living under `skills/` | Manager is the only role that advances the stage. |
| Skill | a markdown document with two-field frontmatter in the project/vertical/global library (`skills/store.py`, `skills/layered.py`) | `skills/` as the home of the stage machine, RL gates and loop mixins | After phases 2-5 `skills/` holds only the library. |
| Doctor / Terminal | `doctor/` and `terminal/` (phase 6) | `maintenance/` and `cli/` as package names | `argus_doctor.py` at the repository root is the stdlib-only bootstrap doctor; `argus_skill/maintenance/` is the runtime Doctor. |
| Overlay vs data domain | Overlay: `domains/<name>/overlay.py`, composed onto a workflow vertical. DATA domain: a project-local, Manager-routed vertical stored as JSON (`verticals/_data_domain.py`) | — | Docstrings distinguish the two now; renaming `domains/` to `overlays/` awaits decision card 8. |
| Mission | `BacklogItem` (class name unchanged); one bounded backlog item inside a Project | — | See "Hierarchy and cardinality" above. |
| Curator | a daemon-resident component owned by `team/curator.py` (a thread of the daemon that owns the teammate pool), not a fifth persistent Role | — | It has routing/model/effort entries in `core/role_config.py` but is not in `RoleName`; decision card 2's recommended default. |
| Permanent aliases | `--life-dir`, `sid`, the `ARGUS_SKILL_*` environment variables | — | Never removed; running daemons and installed launchers pass them. |
