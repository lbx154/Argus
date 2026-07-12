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
- `manager/repl.py`: conversation loop and command dispatch.
- `manager/repl_input.py`: prompt_toolkit/readline/live-cockpit input engines.
- `manager/repl_help.py`: command catalog, help grouping, prompt framing.
- `life/supervisor/_core.py`: mission scheduling and lifecycle orchestration.
- `life/supervisor/_planner_rendering.py`: planner context and reviewer feedback
  rendering.

Compatibility aliases remain in the original modules while downstream imports
migrate. New responsibilities should be added to the focused module rather than
growing the orchestration entry point again.
