# The TypeScript core

Goal: on a machine with only Node, `npm i -g argus` installs Argus and a person can run a
whole research project from one sentence in the web cockpit, which `argus` serves. Every backend
Argus drives (Codex, Claude Code, Copilot CLI, Pi) is an npm package, so its users already
have Node; Python is the second runtime this removes.

The core is small on purpose. It hands work between roles, delivers messages, records what
happened and keeps the permission boundary. It does not wait, poll, watch for stalls, retry,
or guess what a role meant: the backend runs its own turn, and a failure is recorded as it
happened. Whatever the Python runtime does beyond that is left out rather than ported.

## Milestones

| | What works | Status |
|---|---|---|
| M1 | One sentence to a reviewed delivery: Manager, Engineer, Reviewer, back to the Manager, on Codex and Claude Code | done: a real Codex run went from one sentence to a reviewed delivery (3 calls, $0.42, 5.7 min); a grokking replication handed whole to one Engineer took 24.6 min and $2.05 against 15 min and $1.40 for Codex alone, with the same findings |
| M2 | Planner for work too large for one task, its independent tasks side by side; operator questions and answers; the operator writes to the Manager at any time | built and tested with a scripted backend; a real Manager answered in 19 s while an Engineer worked; a real multi-task Planner run is still to come |
| M3 | `argus`: the web cockpit as the way in (no subcommands), its progress map, drafts, attachments, settings, trash, the code workspace, search, resources and the skill library | built; the milestone check runs a project through the web API; the trial ran on it with real Codex projects |
| M4 | one npm package (no runtime dependencies), Copilot and Pi, macOS and Windows, desktop sidecar | the package: `npm i -g` on a machine with only Node runs a project to the Manager's report (with a scripted codex); on Linux, macOS and Windows, CI (`core.yml`) runs the core's tests and installs it with npm to run a project; Copilot and Pi: real Engineer and Reviewer turns, resumed, under bubblewrap; desktop sidecar: the desktop app carries the package and runs it with the system's Node, in place of the frozen Python backend; CI (`desktop.yml`) builds its unsigned installers on Windows, macOS and Linux and its tests start the shipped Argus with Node |
| M5 | new projects default to the TS core; Python serves old projects until they end, then goes | |

## Process and state

One Node process owns a state root and everything in it: there are no file locks. The root
is `$ARGUS_HOME` (default `~/.argus`), separate from the Python runtime's `~/.argus-skill`, so
the two never write the same files. A process takes the root with `$ARGUS_HOME/owner.json`,
created with `O_EXCL` and holding its pid; a stale file (dead pid) is replaced.

A project is a directory `$ARGUS_HOME/projects/<id>/` with:

| File | Holds | Written |
|---|---|---|
| `project.json` | the current state: workdir, request, the operator's name for it, backend, model and effort, tasks, letters waiting for the Planner and for the Manager, role sessions, the open questions, status | replaced atomically (write temp, rename) |
| `events.jsonl` | what happened, in order: messages, hand-offs, calls and their steps (what a role says, the commands it runs, the files it changes, as its turn goes on), task changes | appended |
| `usage.jsonl` | one record per backend call: tokens, cost | appended |
| `calls/<call>.prompt.md`, `calls/<call>.jsonl` | the prompt as sent, and the backend's raw output: a turn can be read and replayed | written by the call |
| `draft.json` | the web composer's unsent draft and its revision | replaced on each save |

`project.json` is the only place a fact lives; events record changes and are never folded
back into state. The work itself stays in the workdir.

## Roles and letters

A hand-off is a letter and its addressee. Every turn ends with `{ "to": ..., "letter": ... }`
as its final answer, and the backend CLI holds the model to that shape (Codex
`--output-schema`, Claude `--json-schema`), or, with no such answer, takes it as the arguments
of a `hand_off` tool whose call ends the turn (Copilot, Pi), so a turn cannot end in prose that
only reads like a hand-off. The addressee is the one thing the host reads, and it is always
someone who acts on the letter: never a state such as done. The letter is free text, passed to
its reader as written: they see the workspace, Argus's record and the letter, not the writer's
conversation, so a letter that hands on new work is that work's whole brief. The operator reads
every letter, so the letters are the project's timeline. A hand-off Argus refuses goes back to
its writer once, in the same session, with the reason; refused again, or a turn that fails, is
a failed call, recorded as such.

| Role | Writes to |
|---|---|
| Manager | `engineer`: the work, whole, when one person can carry it through, opened as a task, the letter its brief; `planner`: work too large for one task, whose later parts depend on what the earlier ones find, or word on the work it runs; `operator`: the answer, report or question |
| Planner | `engineer`: the next tasks, a letter each, each a result to carry through and its letter its brief; `manager`: the goal is met, to report, or the work is stuck; `operator`: a question it waits on |
| Engineer | `reviewer`; `engineer` (keeps working); `planner` or `manager`: the task cannot go on as its giver briefed it, or the work is stuck; `operator` |
| Reviewer | `engineer`: what to repair before the operator sees it; `planner` or `manager`: its judgment, accepted or what is wrong, to whoever gave the task; `operator` |

A letter to the Engineer opens a task; a letter from the Engineer or its Reviewer to anyone but
the two of them closes it, and stays on it as its last word. Nothing is done or finished: the
host never decides whether a task succeeded, the letter says so. Open tasks run side by side,
in the same workspace. The Planner cuts work too large for one task into a few large ones, each
ending in a result rather than a plan for a later one; those that do not depend on each other
it opens together, a letter each, and it reads their letters together when all have ended.
Letters to the Manager are answered one after another. A question to the operator holds up
only its asker: its task, or the Planner, waits for the answer while every other task goes on,
and the page shows each open question as a card of its own. A task's form is made for it: on
a task the Manager gave, the Planner, which never had it, is not among the addressees. There
are no stages: what a research project calls its stages is the order of the tasks the Planner
writes. The work rests when no letter waits for anyone but the operator: `asking` while
questions are open, else `idle`, which is after the Manager's report; the Manager may also
finish a small request in its own turn.

## The skill library

A skill is what was learned about a kind of work, kept as a folder whose `README.md` says in its
first line what the work is: Argus's own in `packages/core/skills/<name>/`, which an upgrade
replaces, and the roles' and the operator's in `$ARGUS_HOME/skills/<name>/`, where one takes the
place of Argus's of the same name. It holds the pitfalls past projects hit and what good work
looks like, not steps, files or who does what, and nothing a capable model already knows: Codex on
its own did a grokking study well in 15 minutes for $1.40, while an Argus project on the same study
spent 46% of its tool-output tokens rereading the research skill's manuals.

The library is the roles'. Argus gives it a place, lists it and shows it, and nothing else: every
role's prompt lists each skill by its README's first line and path and tells the role the library
is its own, every call reads the skills, and a role that writes (the Manager, the Engineer) may
write `$ARGUS_HOME/skills/`. When to write a skill, what to change in one and whether to install a
plugin are the roles' to decide: there is no reflection after a task, no scoring, curating, recall
or approval, and nothing records which skill a project used. Argus's own are `research`,
`software` (a change that builds, whose tests ran, and that does what was asked),
`argus-maintenance` (changing this core) and `learning`, on writing a skill worth keeping: what an
agent can run or recheck, proven on a real case.

The web cockpit shows the library: its skills, and as its learning feed the files of
`$ARGUS_HOME/skills/`, newest first, so a skill a role writes appears while the work goes on. A
plugin is a skill whose folder holds `plugin.json` (`{ name, description, version, url }`):
whoever installs it starts it, and the cockpit lists it among its plugins and opens its url. The
wiki the page also asks for is empty.

## The web cockpit

`argus` serves the cockpit (on 127.0.0.1, or on `--host`, `0.0.0.0` for every network; project
requests always need a bearer token, `ARGUS_WEB_TOKEN` or one made at each start and printed in the
address, since the roles' sandbox shares the machine's network)
and runs the work of its projects in its own process, projects side by side. The page reads files
as they are: the snapshot from `project.json` and `usage.jsonl`, events and the
conversation from `events.jsonl` in the cockpit's event vocabulary, results from the workdir's
files created or changed since the project began. The operator may write at any time: the
Manager takes the message up in its next turn, at once unless it is answering a letter from the
team, while the team's work goes on, and what its letter hands on joins that work. The message
is recorded when the Manager takes it up, so the Manager's letter after it is its reply; the
message's stream follows that turn and ends with the reply. Stop interrupts every call under
way on the project and Start resumes. The live event stream is server-sent events, in both
runtimes.

The composer's draft is kept in the project's directory, so a reload or another device picks it
up; a save names the revision it replaces and is refused over one it never saw. Files attached to
a message are uploaded as they are picked, small ones at once and a large one in parts, into the
workdir's `.argus/attachments/<id>/`, out of the results; the message names their paths and the
Manager reads them, so nothing in Argus reads what they hold. Deleting a project stops its work
and moves its directory to `$ARGUS_HOME/trash/<id>-<time>`, its workdir left as it is; while the
interrupted calls are still recording how they ended, the deletion is refused rather than waited on.
The trash lists what it holds, from each entry's `project.json`, and a restore moves the directory
back unless a project with its id exists.

What the operator sets on a project is in its `project.json`: its workdir, any existing directory,
given when the project is made or later; its name, which the project list and the snapshot show,
an unnamed project named by its request; and the backend, model and reasoning effort every role's
call uses, written by the Python runtime's setting names, a per-role model refused rather than half
applied. A model or effort is the backend's own word for it, passed to its command line as one
argument; the model chooser offers the one set and takes any other by name. A new workdir or
backend clears the role sessions, which belong to the directory and the backend that started them,
and resetting the conversation clears the Manager's. While work is under way, a change is made in
the record that work holds, for the calls after it; a new workdir or backend is refused until the
work stops, since the calls under way work in them. The sidebar's pins, archive
and follow-up marks are kept in `$ARGUS_HOME/browser-state.json`, and `/api/projects/costs` gives
each project's spend from the same figures as the project list.

The code workspace is the workdir, read-only: its tree, which the composer's `@` completion lists
too, a file's text or bytes, and what the system `git` says of it when the workdir is a
repository of its own. A path there passes the check a result's does: inside the workdir, not
hidden. An HTML result is previewed as one page, the local files it names inlined as `data:` URLs,
for the page's sandboxed frame, with a policy that lets its own scripts and styles run while it
can call nothing, this server least of all. Results download one by one; no zip is made. A task
opened from the list is its card on the map with every letter written about it and the error of
its last call; a role's letters download as Markdown; the search reads each project's request,
conversation and letters as they are, with no index. The machine's CPUs and memory (from Node)
and NVIDIA GPUs (from `nvidia-smi`) are read when the page asks, never polled.

The page offers only what the server declares in `/api/meta`. The core declares the required
capabilities, `project.attachments.v1`, `system.resources.v1`, `project.workdir.v1`,
`project.config.v1` and `project.cost-feed.v1`; the page hides the slash commands, the doctor among
them (`composer.commands.v1`), and in the settings the advisor (`advisor.config.v1`) and the light
model, which the core's settings do not list. The Python server declares every capability.

The progress map reads the same files: a card per task, its brief the letter that opened it, and
on it a step per letter written about it, by whom to whom. A task opened in the reader lists its
calls in order, each with the letter it ended with and the path of its raw output
(`calls/<call>.jsonl`). Nothing on the map is inferred: no relations between tasks beyond their
order, no written explanations.

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
Without `bwrap` (macOS, Windows) the CLI's own sandbox mode is used: Codex's
`workspace-write`, on Windows its sandbox that needs no administrator
(`windows.sandbox="unelevated"`); Claude Code's `acceptEdits`, edits without commands.

Starting the CLI: no shell is involved. On Windows npm installs a CLI as a `.cmd` shim, which
only `cmd.exe` runs; the agent layer runs the file the shim names instead, a script with
Node.

A turn ends when everything it started has ended, so a job the Engineer leaves running
(`nohup … &`, `setsid`) is waited for, not killed (`executeProcess` in the runtime). The CLI's
environment carries `ARGUS_TURN`, a value of the turn's own that whatever it starts inherits
however it detaches; after the CLI exits the turn waits, looking every 5 seconds, until nothing
carries it, stays in the CLI's process group or holds its output. Under bubblewrap a shell in
the sandbox also waits, as a sandbox ends with its first process. Cancelling ends all of it. On
Windows a process cannot read another's environment and Node has no job object, so a turn there
waits only for what holds its output, and cancelling reaches the CLI's process tree
(`taskkill /T`) only while the CLI runs: a job it left detached runs on, unseen.

## The package

`packages/core` is the npm package, `@lbx154/argus` until the name `argus` is free. Its build
(`npm run build`, and `npm pack` before packing) makes `dist/argus.mjs`, the program with
`@argus/contracts` and `@argus/runtime` bundled in by esbuild, so the package has no runtime
dependencies. Beside it the package carries the prompts it reads, Argus's own skills, and `web/`,
the web cockpit, built from `frontend/web`'s source when the package is packed (`npm run web`,
6 MB): not the committed `frontend/web/dist`, which keeps every earlier release's files for open
tabs (482 MB) and was once older than the source it came from.

`packages/core/scripts/check-pack.sh` checks the milestone: it packs the package, installs it
with `npm i -g` in a `node:22-slim` container with no Python and no network, starts `argus` and
runs a project through the web cockpit's API (`test/fixtures/e2e.mjs`, as the page does) from one
sentence to the Manager's report, with `test/fixtures/fake-codex.mjs` in place of codex.

## The desktop app

The desktop app (`desktop-tauri/`, Tauri) carries the package as a resource directory `argus-core/`
in the package's layout (`dist/argus.mjs` beside `prompts/`, `ways/` and `web/`) and runs it with
the Node.js on the machine: `node argus.mjs --port P --backend B`, the backend being the CLI the
operator chose. Every backend Argus drives is an npm package, so its users have Node; the app
looks for one of at least 22 beside the chosen CLI, on PATH and in the usual install directories,
and without one says so and where to get it, rather than installing it. It gives Argus its Web
token as `ARGUS_WEB_TOKEN` and its state root as `ARGUS_HOME`; the line `Argus is at <url>` is
the only sign Argus is ready and where, and its exit the only sign it failed. The desktop does not
poll it or restart it; it stops it (SIGTERM, or `taskkill /T` on Windows) when asked or when it
quits. An Argus the desktop lost (it crashed) is known by answering `/api/meta` as authenticated
to the desktop's token, and is stopped before a new one starts. No Python is bundled.

## Left out

Stage machines, verticals as code, completion certificates, observers, Manager supervision,
reflection, recall, the wiki, the map's canvas and notes, trial metering, idle watchdogs, CPU
liveness checks, retry ladders, provider circuits and error-text classification. Some may come
back as plain features later; none is needed to run a project.
