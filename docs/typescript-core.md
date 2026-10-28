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
| M3 | `argus serve`: the web cockpit's 18 essential endpoints, events over SSE, the built web UI | built; the cockpit ran a scripted project end to end and two real Codex Manager turns in a headless browser |
| M4 | one npm package (no runtime dependencies), Copilot and Pi, macOS and Windows, desktop sidecar | the package: `npm i -g` on a machine with only Node runs a project to done (with a scripted codex); on Linux, macOS and Windows, CI (`core.yml`) runs the core's tests and installs it with npm to run a project; Copilot and Pi: real Engineer and Reviewer turns, resumed, under bubblewrap |
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

## Roles and letters

A hand-off is a letter and its addressee. Every turn ends with `{ "to": ..., "letter": ... }`
as its final answer, and the backend CLI holds the model to that shape (Codex
`--output-schema`, Claude `--json-schema`), or, with no such answer, takes it as the arguments
of a `hand_off` tool whose call ends the turn (Copilot, Pi), so a turn cannot end in prose that
only reads like a hand-off. The addressee is the one thing the host reads: it is one of the
places the role can write to, and routing is that and nothing else. The letter is free text,
passed to its reader as written: they see the workspace and the letter, not the writer's
conversation, so a letter that hands on new work is that work's whole brief. The operator reads
every letter, so the letters are the project's timeline. A hand-off Argus refuses goes back to
its writer once, in the same session, with the reason; refused again, or a turn that fails, is a
failed call, recorded as such.

| Role | Writes to |
|---|---|
| Manager | `engineer`: one coherent piece of work, the letter its brief (the ticket); `planner`: work in dependent steps, the letter its brief; `operator`: a question; `manager`: the letter is the answer |
| Planner | `engineer`: the next task, the letter its brief; `manager`: the process is stuck; `operator`: a question; `done` |
| Engineer | `reviewer`; `engineer` (keeps working); `planner`: the task is mis-scoped; `manager`: the process is stuck; `operator` |
| Reviewer | `done` accepts; `engineer` returns repairs; `planner`; `manager`; `operator` |

The Planner hands out one task at a time; work that only differs in its inputs is one task,
which the Engineer runs side by side. There are no stages: what a research project calls its
stages is the order of the tasks the Planner writes. A project is done when the Reviewer
accepts the task of a ticket the Manager gave the Engineer, or when the Planner writes `done`.
The Manager may finish a small request in its own turn.

## The web cockpit

`argus serve` (127.0.0.1 only; `ARGUS_WEB_TOKEN` makes project requests need that bearer) runs
the work of its projects in its own process: one Flow call at a time per project, in the order
asked for, projects side by side. The page reads files as they are: the snapshot from
`project.json` and `usage.jsonl`, events and the conversation from `events.jsonl` in the
cockpit's event vocabulary, results from the workdir's files created or changed since the
project began. A message's stream follows the Manager's turn and ends with its reply; the team's
work goes on after it. A message sent while work runs waits until it stops; Stop interrupts the
call under way and Start resumes. The live event stream is server-sent events, in both runtimes.

## The agent call

The only boundary between the flow and the backends:

```ts
callAgent(request: AgentRequest): Promise<AgentResult>
```

(`packages/core/src/types.ts`). The agent layer runs the CLI with the prompt on stdin and
the form's schema for its final answer, reads its JSON event stream, takes only the
backend's own completion event as success (Codex `turn.completed`, Claude `result` with
`subtype: success`, Copilot `result` with `exitCode: 0`, Pi `agent_settled` after a last answer
that did not fail), and judges the hand-off the turn ended with: Codex's last message, Claude's
`structured_output`, or the `hand_off` call in the last message of Copilot's main agent or of
Pi. The shape is already the strict one OpenAI's structured outputs take: both fields required,
no other allowed.

Copilot and Pi have no final answer held to a schema, so each call writes an extension that
registers `hand_off` with the form's schema: for Copilot a plugin (`--plugin-dir`) whose tool is
`isTerminal`, its experimental extensions enabled for the run by
`COPILOT_CLI_ENABLED_FEATURE_FLAGS=EXTENSIONS` (`--experimental` would turn them on in the
operator's settings for good); for Pi `--extension` with a tool returning `terminate: true`. Pi
checks the call against the schema, so the model corrects a malformed form inside its turn;
Copilot does not, and Argus's judgment is the check.

Usage: Codex from the session's rollout file (`token_usage_record`, per request); Claude from
`result.usage` (input counts include cache reads and writes) with `total_cost_usd` as the cost;
Copilot from the session's event log (`session-state/<id>/events.jsonl`, a
`session.usage_record` per request, subagents' included), its AI credits as the cost ($0.01
each); Pi from each assistant message's `usage`, with `cost.total` as the cost.

Sandbox: on Linux with a working `bwrap`, the CLI runs under bubblewrap: system directories
read-only, the workdir and the task's write directories writable, a private home per workdir
with the CLI's login file passed through, and only the environment the CLI needs to sign in.
A shell waits after the CLI until everything it started has ended, so a detached job is not
killed when the turn ends. Without `bwrap` (macOS, Windows) the CLI's own sandbox mode is
used: Codex's `workspace-write`, on Windows its sandbox that needs no administrator
(`windows.sandbox="unelevated"`); Claude Code's `acceptEdits`, edits without commands.

Starting the CLI: no shell is involved. On Windows npm installs a CLI as a `.cmd` shim, which
only `cmd.exe` runs; the agent layer runs the file the shim names instead, a script with
Node. Cancelling ends the CLI's whole process tree: its process group on POSIX,
`taskkill /T` on Windows.

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
