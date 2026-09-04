# Argus Core Concepts

This document defines the canonical runtime hierarchy and terminology for Argus
at commit `ae2daa1fbc2c918b4e7126151fe55eb68fd0cb98`
(`https://github.com/lbx154/Argus.git`). It complements the existing
`plugins/argus/CONTEXT.md` glossary, which can link here for this runtime model.
Chosen model: `Operator -> Projects -> Missions -> Roles -> role/provider sessions or turns`.

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

`OperatorContextStore` is physically instantiated with one `life_dir` and writes
`operator_context.jsonl`, `operator_context.json`, and `operator_context.lock`
under that directory. Records carry `mission`, `project`, or `global` labels, and
projections sort by scope precedence, filter by role, and bind bounded
directives to the current mission
[`argus_skill/core/operator_context.py:15-476`]. In the current implementation,
`global` affects precedence and visibility within that project state directory;
it does not automatically create one Operator-level record shared across all
Projects. That is an implementation boundary, not a criticism of the concept.

## Concept-to-storage mapping

| Concept | Current storage surface |
| --- | --- |
| Project root | The execution `workdir` recorded in session metadata and passed into daemon/runner configuration. |
| Project state directory / `life_dir` | `~/.argus-skill/projects/<project-id>/`, also called session state by code and used as the root for project-owned runtime files. |
| Backlog and mission state | `backlog.jsonl` for live mission rows plus `backlog.archive.jsonl` for terminal rows. |
| Events | `events.jsonl` and retained rollovers under `life_dir`; this is the durable replay surface for supervisor/runtime events. |
| Handoffs | `handoffs/<mission-id>/` contains `mission.json`, `CHECKPOINT.md`, `frontier.json`, `latest.json`, and round handoffs [`argus_skill/life/context_packet.py:17-344`]. |
| Role sessions | `role-sessions/<role>.json` capsules, including Planner, Engineer, Reviewer, and teammate state where a role has a durable provider context. |
| Operator context | `operator_context.jsonl` ledger plus `operator_context.json` projection and lock in the same project state directory. |
