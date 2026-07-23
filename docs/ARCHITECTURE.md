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
            └─ SupervisedEngineer          engineer/runner.py
                 ├─ allowed low-risk work: Engineer self-review → done
                 └─ required/requested review: Reviewer-until-done
                                              reviewer/_core.py (+ reviewer_schema.json)
  sits on:
    core/  (budget, persistence, structured I/O, paths, locks)
    verticals/<name>/  (the task-specific shape + reviewer gate)
    backend: agent_cli/ + adapters/  (codex / claude / copilot / opencode CLI runners)
```

## Module map (live)

| Area | File(s) | Role |
|---|---|---|
| Entry / CLI | `argus_skill/__main__.py`, `apps/cli/_parser.py`, `apps/cli/_core.py` | argument parsing + one-shot action dispatch (`--daemon`, `--daemon-stop [--drain]`, `--status`, …) |
| Manager | `manager/_core.py`, `manager/front_door.py`, `manager/dispatch.py`, `webapi/manager_bridge.py` | model-judged chat-vs-task decision, vertical selection, durable dispatch, and the **sole authority for pipeline stage transitions** (`current_stage`) — Planner/Engineer/Reviewer may only advise, never write it themselves |
| Cockpit | `frontend/tui/`, `frontend/web/`, `webapi/server.py` | Ink/Web operator surfaces; there is no Python line REPL |
| Runtime wiring | `apps/_runtime.py` | builds the live runner / supervisor from config |
| Mission scheduler | `life/supervisor/_core.py` | the 7×24 outer loop: claim backlog → run mission → plan next; budget, lifecycle, drain |
| Skill loop | `loop.py` | per-mission glue: build engineer prompt → run → select self-review or independent review |
| Engineer | `engineer/runner.py` | `SupervisedEngineer` round-loop control flow; may explicitly self-verify allowed low-risk bounded work |
| Reviewer | `reviewer/_core.py`, `reviewer/reviewer_schema.json` | independent `done` / `continue` / `blocked` verdict when required by the vertical/task or requested by Engineer |
| Planner | `planner/planner.py` | L4 continuous planner: next tasks |
| Core (dumb pipe) | `core/models.py`, `core/ports.py`, `core/paths.py`, `core/pricing.py`, `core/daemon_lock.py`, `core/bootstrap.py` | budget, persistence, structured I/O, paths, locks |
| Verticals | `verticals/_base.py` + `verticals/{research,math,physics,materials,quant,speedrun,nanochat,nanogpt_speedrun,kernelbench,learning,ale_last_exam,...}/` | workflow/deliverable shape via a plugin contract (`stage order`, `role_banner`, `completion_gate`, `search_altitude`); `research` owns the full paper lifecycle and `ale_last_exam` is the single-stage hidden-reference artifact-delivery shape |
| Domains | `domains/_base.py` + `domains/{chemistry,...}/` | optional specialization composed with `research`; a domain may add role context, mandatory checklist floors, and matchable Skills, but never replace research stages or completion |
| Daemon | `daemon/life_worker.py` | detached 7×24 worker around `LifeSupervisor`; SIGTERM/drain, pid lock |
| Backend | `agent_cli/agent_cli_runner.py`, `adapters/agent_cli_backend.py`, `adapters/memory_backend.py` | the CLI runner (codex/claude/copilot/opencode) + a deterministic memory backend for tests |

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
2. **Round k.** `SupervisedEngineer.run` builds the Engineer prompt and executes
   the work. The Engineer writes structured control with `review=skip|required`.
3. **Classify.** If self-review is enabled, the task/vertical does not require
   independent review, and the Engineer explicitly selects `skip`, the runtime
   records `review_source=engineer_self_review` and ends `done`. The prompt limits
   `skip` to low-risk work with a passing verifier; the harness intentionally does
   not add a second heuristic or validator to overrule that agent judgment.
   Otherwise a fresh Reviewer returns `done`, `continue`, or `blocked`;
   `continue` iterates up to `max_rounds`. `stage_closing`, `review:required`, and
   vertical-wide independent-review policy disable the self-review path. The
   harness records the selected authority and never infers completion from prose.
4. **Plan next.** Between missions the planner proposes a persisted backlog DAG.
   Every batch receives an opaque `plan_id`, `plan_version`, and stable node
   keys. By default Dynamic Plan is off. In `shadow` mode the Reviewer can emit
   a structured `reconsider` signal without changing execution. In `active`
   mode, consecutive signals end the current mission as `replan_requested`;
   the existing planner gate runs L4, and one locked backlog rewrite preserves
   completed nodes, marks the old active nodes `superseded`, and installs the
   replacement DAG. Any planner, validation, conflict, or write failure keeps
   the old plan runnable.
5. **Disclose context progressively.** Replacement nodes carry only bounded
   `context_refs` (artifact path, reason, optional content hash). The Engineer
   decides which referenced artifacts to open; the harness never injects their
   full contents or guesses scientific relevance.

## Tests as living docs

`tests/test_loop_smoke.py` documents the core behaviour contract (distill on
miss then converge, blocked short-circuits, max-rounds stays bounded). The
daemon lifecycle (drain-stop, signal handling) is covered in
`tests/daemon/test_life_worker.py`.
