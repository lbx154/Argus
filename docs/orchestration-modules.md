# Orchestration Module Boundaries

> Current module ownership. See
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md). Entry modules coordinate; they
> must not absorb state models, process adapters, prompt bodies, or lifecycle
> algorithms again.

## Operator and Web surfaces

- `apps/cli/_parser.py`: top-level CLI arguments.
- `apps/cli/_core.py`: CLI action dispatch; focused helpers live beside it.
- `webapi/server.py`: application assembly and compatibility exports.
- `webapi/routes/`: HTTP/SSE route handlers.
- `webapi/project_state.py`: project list and snapshot read model.
- `webapi/artifacts.py`: workspace-confined artifact allowlist and previews.
- `webapi/manager_bridge.py`: stateful Web/Ink Manager conversation bridge.

## Manager control plane

- `manager/_core.py`: thin `Manager` composition shell and public result types.
- `manager/_front_door_ops.py`: Manager SELF/front-door operations.
- `manager/_vertical_ops.py`: vertical/domain selection and `divide`.
- `manager/_stage_ops.py`: the sole pipeline-stage writer.
- `manager/_maintenance_ops.py`: bounded framework self-maintenance decisions.
- `manager/front_door.py`: durable operator handoff and GoalContract recording.
- `manager/dispatch.py`: lifetime selection and task dispatch.
- `manager/stage_decider.py`: parsed stage-decision semantics.

## Runtime construction and execution

- `apps/_runtime.py`: compatibility facade/export surface.
- `apps/_runtime_construction.py`: backend, SkillLoop, Manager and store wiring.
- `apps/_runtime_supervisor.py`: `LifeSupervisorConfig` construction.
- `apps/_runtime_execute.py`: one mission's execution, result extraction and
  Manager stage handoff.
- `apps/_runtime_backends.py`: deterministic/scripted test backends.
- `core/run_gateway.py`: the only application-level backend invocation gateway.
- `adapters/agent_cli_backend/`: provider process admission, spawn, I/O and
  result finalization.

## Mission loop

- `loop.py`: per-mission glue: Skill selection/adaptation, Engineer/Reviewer
  loop, outcome and reusable-memory settlement.
- `engineer/runner.py`: round-loop composition.
- `engineer/round_*.py`: prompt, execution, wait, Reviewer and settlement phases.
- `reviewer/_core.py`: independent review invocation and structured verdict.
- `roles/prompts/`: canonical prompt composition for all four resident roles.

## 7×24 supervisor

- `life/supervisor/_core.py`: public `LifeSupervisor` composition and main
  driving loop.
- `life/supervisor/_config.py`: runtime configuration types.
- `life/supervisor/_mission_execution*.py`: claim, execute, stage guard and
  mission settlement.
- `life/supervisor/_planning_cycle*.py`: Planner intake, verdict, completion,
  dedupe and atomic enqueue/revision.
- `life/supervisor/_planning_context.py`: project/history/contract context and
  completion-gate reads.
- `life/supervisor/_idle_cycle.py`: idle/terminal rendering and state.
- `life/supervisor/_lifecycle.py`: Project lifecycle completion.
- `life/memory.py`: backlog, EventJournal projection and memory bundle.

New supervisor behavior belongs in the narrow phase module that owns it. Do not
add another full implementation to `_core.py` for convenience.

## Daemon

- `daemon/life_worker.py`: compatibility facade and public lifecycle entry.
- `daemon/_life_worker_boot.py`: boot/readiness/state-root construction.
- `daemon/_life_worker_run.py`: run loop and clean shutdown.
- `daemon/_life_worker_identity.py`: runtime/release identity.
- `daemon/state.py`: continuous config, status/log sidecars and stop/drain/kill.
- `daemon/commands.py`: durable idempotent command receipts.
- `daemon/protocol.py`: daemon protocol/capabilities.

## Ownership rule

When adding behavior, first identify its state owner:

- operator intent or stage -> Manager;
- future work/DAG -> Planner + planning cycle;
- task execution -> Engineer;
- completion evidence -> Reviewer;
- mechanical persistence/scheduling -> supervisor/core;
- provider process details -> adapter;
- presentation -> Web/TUI read models and reducers.

If a change needs the same policy text or condition in two owners, extract one
typed contract or one canonical renderer instead of copying it.
