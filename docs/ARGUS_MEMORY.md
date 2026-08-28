# Argus Memory Design

Argus memory is a layered, file-backed system that turns execution into resumable state and reusable knowledge.

## Memory Layout

```text
Operator memory   — what the operator wants and permits
Curated memory    — what later work should reuse
Working memory    — where current work stands
Execution memory  — what happened
```

**Execution memory** is the project’s observable event history: lifecycle events, selected progress, role outputs, verdicts, costs, and provider I/O when enabled (`life/event_log.py`). Hidden provider reasoning is unavailable. In the default `signal` mode, Argus retains selected high-value events rather than every progress message.

**Working memory** is the resumable state of projects and missions: backlog, frontier, checkpoint, reviewed handoffs, and role-session capsules (`life/context_packet.py`, `core/role_session.py`). It lets a later role continue from the current frontier without replaying the full trajectory.

**Curated memory** is knowledge selected for reuse: Wiki pages hold declarative project knowledge, Skills hold procedures, and failure-experience capsules preserve verified lessons from unsuccessful missions (`wiki/store.py`, `skills/layered.py`, `life/failure_experience.py`). Its scope may be project, vertical, or shared profile.

**Operator memory** stores directives, preferences, capabilities, and revocations in OperatorContext (`core/operator_context.py`). Records can have mission, project, or global scope, but the current store is project-local; `global` does not automatically propagate across projects.

## How Memory Is Produced

```text
execution
  → observable events
  → frontier, checkpoint, and handoffs
  → post-mission curation
  → retrieval by later roles
```

While roles work, Argus records observable events. At execution boundaries, it compresses current state into the frontier, checkpoint, handoffs, and role capsules. After settlement, stable facts can enter the Wiki, reusable procedures can enter Skills, and verified failures can become failure experience. Later roles retrieve each layer according to project, mission, role, and authority.

A proposed always-loaded behavioral Skill asks every role to state important insights immediately in natural-language reasoning or progress output. Argus would persist that observable statement, and post-mission analysis would inspect relevant role trajectories and consolidate lessons worth retaining.

This description reflects `lbx154/Argus` at commit `ae2daa1fbc2c918b4e7126151fe55eb68fd0cb98`.
