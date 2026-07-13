# Orchestration Module Boundaries

Large entry points retain coordination, while read models, adapters, rendering,
and process state live in dedicated modules:

- `webapi/server.py`: FastAPI routes and write-side command orchestration.
- `webapi/project_state.py`: project list and snapshot read model.
- `webapi/artifacts.py`: workspace-confined artifact allowlist and previews.
- `apps/_runtime.py`: real SkillLoop/life orchestration.
- `apps/_runtime_backends.py`: deterministic memory and scripted test backends.
- `daemon/life_worker.py`: worker boot and run loop.
- `daemon/state.py`: continuous config, status/log sidecars, stop/drain/kill.
- `webapi/manager_bridge.py`: stateful Web/Ink Manager conversation bridge.
- `manager/front_door.py`: routing, Manager runner, and execution handoff.
- `manager/config_intent.py`: natural-language runtime configuration.
- `manager/dispatch.py`: lifetime selection and durable task dispatch.
- `life/supervisor/_core.py`: mission scheduling and lifecycle orchestration.
- `life/supervisor/_planner_rendering.py`: planner context and reviewer feedback
  rendering.

New responsibilities should be added to the focused module rather than growing
the orchestration entry point again.
