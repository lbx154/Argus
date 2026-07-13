# Architecture

> **What Argus is:** an autonomous agent that drives a real public benchmark to
> a target, 7×24, judged by a reviewer. One CLI, one loop, one verticalized
> task shape. The harness is a domain-agnostic dumb pipe (budget, persistence,
> scheduling, structured I/O, anti-cheat guardrails); all research judgment
> lives with the agent (manager / planner / engineer / reviewer — see the
> Manager row below for why it isn't "just" a fourth pipeline stage).

This map covers the **live tree**. It is kept in sync with the code — if a path
here is wrong, fix it (a stale map mis-routes the next maintainer).

## The spine (one path, end to end)

```
argus-skill (CLI)                         apps/cli/_parser.py + apps/cli/_core.py
  └─ __main__.py / entry                  argus_skill/__main__.py
  └─ Manager.divide                       argus_skill/manager/_core.py
       picks ONE vertical (chat vs task, vertical select)
  └─ runtime wiring                       argus_skill/apps/_runtime.py
  └─ LifeSupervisor (mission scheduler)   argus_skill/life/supervisor/_core.py
       └─ SkillLoop                        argus_skill/loop.py
            └─ SupervisedEngineer  ◄────►  Reviewer-until-done
               engineer/runner.py          reviewer/_core.py (+ reviewer_schema.json)
  sits on:
    core/  (budget, persistence, structured I/O, paths, locks)
    regime_jump/  (the SINGLE anti-stuck mechanism: regime-jump)
    verticals/<name>/  (the task-specific shape + reviewer gate)
    backend: agent_cli/ + adapters/  (codex / claude / copilot CLI runners)
```

## Module map (live)

| Area | File(s) | Role |
|---|---|---|
| Entry / CLI | `argus_skill/__main__.py`, `apps/cli/_parser.py`, `apps/cli/_core.py` | argument parsing + one-shot action dispatch (`--daemon`, `--daemon-stop [--drain]`, `--status`, …) |
| Manager | `manager/_core.py`, `manager/front_door.py`, `manager/dispatch.py`, `webapi/manager_bridge.py` | model-judged chat-vs-task decision, vertical selection, durable dispatch, and the **sole authority for pipeline stage transitions** (`current_stage`) — Planner/Engineer/Reviewer may only advise, never write it themselves |
| Cockpit | `frontend/tui/`, `frontend/web/`, `webapi/server.py` | Ink/Web operator surfaces; there is no Python line REPL |
| Runtime wiring | `apps/_runtime.py` | builds the live runner / supervisor from config |
| Mission scheduler | `life/supervisor/_core.py` | the 7×24 outer loop: claim backlog → run mission → plan next; budget, lifecycle, drain |
| Skill loop | `loop.py` | per-mission glue: build engineer prompt → run → review |
| Engineer | `engineer/runner.py` | `SupervisedEngineer` round-loop control flow |
| Reviewer | `reviewer/_core.py`, `reviewer/reviewer_schema.json` | the **sole source of truth for "done"** — no hardcoded completion gate |
| Planner | `planner/planner.py` | L4 continuous planner: next tasks + (optional) meta decision |
| Core (dumb pipe) | `core/models.py`, `core/ports.py`, `core/paths.py`, `core/pricing.py`, `core/daemon_lock.py`, `core/bootstrap.py` | budget, persistence, structured I/O, paths, locks |
| Meta (anti-stuck) | `regime_jump/` (`saturation.py`, `flow_controller.py`, `ledger.py`, `meta_prompter.py`, `config.py`) | regime-jump: DETECT (dumb counter) / JUDGE (planner LLM) / ENFORCE (never-cleared forbidden ledger). Fail-soft to no-op. |
| Verticals | `verticals/_base.py` + `verticals/{nanochat,nanogpt_speedrun,kernelbench,speedrun,quant,research,learning,ale_last_exam}/` | per-task shape via a plugin contract (`role_banner`, `completion_gate`, `search_altitude`, `strategy_pool`); `ale_last_exam` is the single-stage hidden-reference artifact-delivery shape |
| Daemon | `daemon/life_worker.py` | detached 7×24 worker around `LifeSupervisor`; SIGTERM/drain, pid lock |
| Backend | `agent_cli/agent_cli_runner.py`, `adapters/agent_cli_backend.py`, `adapters/memory_backend.py` | the CLI runner (codex/claude/copilot) + a deterministic memory backend for tests |

> **Optional, not the spine:** the `research` vertical (paper-from-idea-to-
> submission) and its `skills/` paper machinery are an OPTIONAL mode, lazy-loaded
> only when the project's vertical is `research`. They are not part of the
> metric-speedrun product and must not be on its default import/identity surface.

## On-disk layout

Global identity / journal / skill state lives at `~/.argus-skill/`. Per-project
state (project card, memory journal, backlog, event log, inbox, daemon pid +
`continuous.json`) lives under `~/.argus-skill/projects/<fingerprint>/`.
Continuous mode is coordinated through each project's `continuous.json` via
`read_continuous_config()` / `write_continuous_config()`; `--status` reports the
current project state alongside the shared global journal.

## How a mission flows

1. **Select.** `Manager.divide` routes the objective to ONE vertical.
2. **Round k.** `SupervisedEngineer.run` builds the engineer prompt, runs the
   vertical's checks, then asks the reviewer for a verdict.
3. **Classify.** Reviewer says `done` (and checks pass) → done; `blocked` →
   surface; otherwise iterate up to `max_rounds`. The reviewer's verdict is the
   only completion authority.
4. **Plan next.** Between missions the planner proposes the next task(s); the
   meta layer may convene a regime-jump when the promoted floor is frozen.

## Tests as living docs

`tests/test_loop_smoke.py` documents the core behaviour contract (distill on
miss then converge, blocked short-circuits, max-rounds stays bounded). The
daemon lifecycle (drain-stop, signal handling) is covered in
`tests/daemon/test_life_worker.py`.
