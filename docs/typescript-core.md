# The TypeScript core

Goal: on a machine with only Node, `npm i -g argus` installs Argus and a person can run a
whole research project from one sentence, in the terminal or the web cockpit. Every backend
Argus drives (Codex, Claude Code, Copilot CLI, Pi) is an npm package, so its users already
have Node; Python is the second runtime this removes.

The core is small on purpose. It hands work between roles, delivers messages, records what
happened and keeps the permission boundary. It does not wait, poll, watch for stalls, retry,
or guess what a role meant: the backend runs its own turn, and a failure is recorded as it
happened. Whatever the Python runtime does beyond that is left out rather than ported.

## Milestones

| | What works | Status |
|---|---|---|
| M1 | `argus run "<request>"` in a directory: Manager, Engineer, Reviewer, done, on Codex and Claude Code | done: a real Codex run went from one sentence to a reviewed delivery (3 calls, $0.42, 5.7 min) |
| M2 | Planner for multi-task work; operator questions and answers; `argus say/answer/status` | built and tested with a scripted backend; a real multi-task run is next |
| M3 | `argus serve`: the web cockpit's 18 essential endpoints, events over SSE, the built web UI | |
| M4 | one npm package (no runtime dependencies), Copilot and Pi, macOS and Windows, desktop sidecar | the package: `npm i -g` on a machine with only Node runs a project to done (with a scripted codex) |
| M5 | new projects default to the TS core; Python serves old projects until they end, then goes | |

## Process and state

One Node process owns a state root and everything in it: there are no file locks. The root
is `$ARGUS_HOME` (default `~/.argus`), separate from the Python runtime's `~/.argus-skill`, so
the two never write the same files. A process takes the root with `$ARGUS_HOME/owner.json`,
created with `O_EXCL` and holding its pid; a stale file (dead pid) is replaced.

A project is a directory `$ARGUS_HOME/projects/<id>/` with:

| File | Holds | Written |
|---|---|---|
| `project.json` | the current state: workdir, request, tasks, role sessions, the open question, status | replaced atomically (write temp, rename) |
| `events.jsonl` | what happened, in order: messages, hand-offs, calls, task changes | appended |
| `usage.jsonl` | one record per backend call: tokens, cost | appended |
| `calls/<call>.jsonl` | the backend's raw output, for debugging | written by the call |

`project.json` is the only place a fact lives; events record changes and are never folded
back into state. The work itself stays in the workdir.

## Roles and hand-offs

Every role's turn ends with its form as the final answer. The backend CLI holds the model to
the form's schema (Codex `--output-schema`, Claude `--json-schema`), so a turn cannot end in
prose that only reads like a hand-off. The form takes its fields and their descriptions from
`packages/contracts/schemas/handoff_forms.json`, only those the flow reads, and the Manager's
form adds `reply`, what the operator reads. Where the work goes next is the form's `to`, and
nothing else: the host reads no prose. A form Argus refuses (its schema or its own check) goes
back to the role once, in the same session, with the reason; refused again, or a turn that
fails, is a failed call, recorded as such.

| Role | Form | `to` |
|---|---|---|
| Manager | `manager` | `team` files the request as a ticket; `operator` asks; `manager`, the reply is the whole answer |
| Planner (M2) | `plan` | `engineer` with tasks; `done`; `operator`; `manager` |
| Engineer | `engineer` | `reviewer`; `done` (no review needed); `engineer` (keeps working); `planner`; `manager`; `operator` |
| Reviewer | `review` | `done` accepts; `engineer` returns repairs; `planner`; `manager`; `operator` |

A project is done when the Reviewer accepts a bounded ticket's task and no other task is
open, or when the Planner hands off `to: done`. The Manager may finish a small request in its
own turn.

## The agent call

The only boundary between the flow and the backends:

```ts
callAgent(request: AgentRequest): Promise<AgentResult>
```

(`packages/core/src/types.ts`). The agent layer runs the CLI with the prompt on stdin and
the form's schema for its final answer, reads its JSON event stream, takes only the
backend's own completion event as success (Codex `turn.completed`, Claude `result` with
`subtype: success`), and judges the form the turn ended with: Codex's last message, in the
strict shape OpenAI's structured outputs take (every field required, an optional one
nullable, a null read as left out), or Claude's `structured_output`.

Usage: Codex from the session's rollout file (`token_usage_record`, per request); Claude from
`result.usage` (input counts include cache reads and writes) with `total_cost_usd` as the cost.

Sandbox: on Linux with a working `bwrap`, the CLI runs under bubblewrap: system directories
read-only, the workdir and the task's write directories writable, a private home per workdir
with the CLI's login file passed through, and only the environment the CLI needs to sign in.
A shell waits after the CLI until everything it started has ended, so a detached job is not
killed when the turn ends. Without `bwrap` the CLI's own sandbox mode is used.

## The package

`packages/core` is the npm package, `@lbx154/argus` until the name `argus` is free. Its build
(`npm run build`, and `npm pack` before packing) makes `dist/argus.mjs`, the CLI with
`@argus/contracts` and `@argus/runtime` bundled in by esbuild, so the package has no runtime
dependencies. Beside it the package carries the prompts the CLI reads and `web/`, the built
web cockpit: only the files `frontend/web/dist/index.html` reaches (6 MB), since that
directory keeps every earlier release's chunks for open tabs (479 MB).

`packages/core/scripts/check-pack.sh` checks the milestone: it packs the package, installs it
with `npm i -g` in a `node:22-slim` container with no Python and no network, and runs a project
from one sentence to done, with `test/fixtures/fake-codex.mjs` in place of codex.

## Left out

Stage machines, verticals, completion certificates, observers, Manager supervision,
reflection and learning, skills and wiki, the map and canvas, trial metering, idle
watchdogs, CPU liveness checks, retry ladders, provider circuits and error-text
classification. Some may come back as plain features later; none is needed to run a project.
