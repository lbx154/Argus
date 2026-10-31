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
| M3 | `argus`: the web cockpit as the way in (including operator skill-store commands), its progress map, drafts, attachments, settings, trash, the code workspace, search, resources and the skill library | built; the milestone check runs a project through the web API; the trial ran on it with real Codex projects |
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
| `project.json` | the current state: workdir, request, the operator's name for it, backend, model and effort, tasks, letters waiting for the Planner and for the Manager, role sessions, the open questions, status, the versions kept of each delivered file | replaced atomically (write temp, rename) |
| `events.jsonl` | what happened, in order: messages, hand-offs (a delivered result's with its files and their versions), calls and their steps (what a role says, the commands it runs, the files it changes, as its turn goes on), task changes | appended |
| `usage.jsonl` | one record per backend call: tokens, cost | appended |
| `calls/<call>.prompt.md`, `calls/<call>.jsonl` | the prompt as sent, and the backend's raw output: a turn can be read and replayed | written by the call |
| `delivered/<version>` | a text file of a delivered result, up to 512 KB, as it was then; a file's last ten copies are kept | written on delivery |
| `editions.jsonl` | the operator's editions of a text result or of an answer, the first with what it was made from | appended |

`project.json` is the only place a fact lives; events record changes and are never folded
back into state. The work itself stays in the workdir.

## Roles and letters

A hand-off is a letter and its addressee. Every turn ends with `{ "to": ..., "letter": ... }` as
its final answer, and the backend CLI holds the model to that shape (Codex `--output-schema`,
Claude `--json-schema`), or, with no such answer, takes it as the arguments of a `hand_off` tool
whose call ends the turn (Copilot, Pi), so a turn cannot end in prose that only reads like a
hand-off. A role's standing instructions, its rules and where things are, go apart from the
turn's message: with every turn where the backend has a place for them (Codex
`developer_instructions`, Claude `--append-system-prompt`), so they outlast its compaction of a
long session, and at the head of a session's first prompt where it has none (Copilot, Pi). The
addressee is the one thing the host routes by, and it is always someone who acts on the letter:
never a state such as done. The Manager's form also names the files of a result it hands the
operator (`open`, empty for any other letter); the page shows them beside the letter as its
result card, as named, without a check. The letter is free text, passed to its reader as
written: they see the workspace, Argus's record and the letter, not the writer's conversation.
When the Manager hands an operator request to the Engineer or Planner, the flow appends that
request's exact text and attachment paths to the brief. The current Manager letter identifies
the request; team mail supplies no operator text. The operator reads every letter, so the
letters are the project's timeline. A hand-off Argus refuses goes back to its writer once, with
the reason (a fresh session for a review); refused again, or a turn that fails, is a failed
call, recorded as such. Nothing retries it: like a question, it holds up only its role, the task
or the Planner, till the operator resumes the work, while the rest goes on.

| Role | Writes to |
|---|---|
| Manager | `engineer`: the work, whole, when one person can carry it through, opened as a task, the letter its brief, or word addressed to an existing task; `planner`: work too large for one task, whose later parts depend on what the earlier ones find, or word on the work it runs; `operator`: the answer, report or question |
| Planner | `engineer`: the next tasks, a letter each, each a result to carry through and its letter its brief, each naming the tasks it waits for; `planner`: nothing to open now, it reads on with the next letter; `manager`: the goal is met, to report, or the work is stuck; `operator`: a question it waits on |
| Engineer | `reviewer`; `engineer` (keeps working); `planner`: the task cannot go on as the Planner briefed it; `manager`: the work is stuck; `operator`: a question only the operator can decide |
| Reviewer | `engineer`: what to repair before the operator sees it; `planner` or `manager`: its judgment, accepted or what is wrong, to whoever gave the task; `operator` |

An untargeted letter to the Engineer opens a task; a letter from the Engineer or its Reviewer to anyone but
the two of them closes it, and stays on it as its last word. Nothing is done or finished: the
host never decides whether a task succeeded, the letter says so. Open tasks run side by side,
in the same workspace, each once the tasks it waits for have closed. The Planner lays out work
too large for one task as large ones, each ending in a result rather than a plan for a later
one, each waiting for the tasks whose results it needs (only tasks opened before it, so none
wait in a circle); it reads its letters as they come, while the tasks go on. A letter that hands
over a result, the Manager's to the operator or one that closes a task, names its files.
Letters to the Manager are answered one after another. A question to the operator holds up
only its asker: its task, or the Planner, waits for the answer while every other task goes on,
and the page shows each open question as a card of its own. A task's form is made for it: on
a task the Manager gave, the Planner, which never had it, is not among the addressees. There
are no stages: what a research project calls its stages is the order of the tasks the Planner
writes. The work rests when no letter waits for anyone but the operator: `asking` while
questions are open, else `idle`, which is after the Manager's report; the Manager may also
finish a small request in its own turn.

## The skill library

The operator installs community packs through `argus skills add <name>` or the cockpit's
Skill store tab, and removes them with `argus skills remove <name>` or Uninstall. `argus skills`
lists each catalog version, installed version, description and `needs`. Both entry points use
`packs.ts`'s catalog/add/remove functions; the HTTP routes require the cockpit bearer token.
The catalog address is fixed in that module, and file addresses derive from its directory.
The catalog contains `packs`, each with `name`, `version`, `description`, optional
`description_zh`, `requires`, `needs`, and `files` (`path`, `size` in bytes). `requires` and
`needs` are arrays of strings. This first version rejects packs with nonempty `requires`;
`needs` describes requirements without installing them.

Downloads reject redirects and catalogs larger than 1 MiB, and give up after 30 seconds. A pack
this version cannot install is left out of the catalog, the rest still listed: one larger than
8 MiB, without `SKILL.md`, named like a bundled skill, or with a file path that escapes the pack,
includes a backslash or has a segment starting with a dot. Fields it does not know are ignored.
Every download must match its declared byte count.
`SKILL.md` must contain a description and a front-matter version matching the catalog.
That front matter is the only installed-version record. A complete validated download is
staged outside the listed roots, in `$ARGUS_HOME/skill-packs.tmp-<name>-*/`, before replacing the
installed directory; a failed download leaves the installed version intact and removes staging.
In the Linux sandbox, all roles receive the pack root as read-only, alongside bundled skills; team write permissions
stay in `$ARGUS_HOME/skills/`.

A skill is what was learned about a kind of work, kept as a folder whose `SKILL.md` says in its
front matter (`name`, `description`), as Codex and Claude Code write a skill, what the work is: Argus's own in `packages/core/skills/<name>/`, which an upgrade
replaces, installed community packs in `$ARGUS_HOME/skill-packs/<name>/`, and the team's (the roles' and the operator's) in `$ARGUS_HOME/skills/<name>/`, listed
beside Argus's, a name shared or not, so that an upgrade of Argus's own reaches every project. It
holds where good work starts, what past runs measured and what good work looks like, not steps,
files or who does what, and nothing a capable model already knows: Codex on
its own did a grokking study well in 15 minutes for $1.40, while an Argus project on the same study
spent 46% of its tool-output tokens rereading the research skill's manuals.

The library is the roles'. Argus gives it a place, lists it and shows it, and nothing else: every
role's standing instructions list each skill by its description and path and tells the role the library
is its own, every call reads the skills, and the Engineer may write `$ARGUS_HOME/skills/`;
the Manager, Planner and Reviewer read it. When to write a skill and what to change in one
are the roles' to decide: there is no reflection after a task, no scoring, curating, recall
or approval, and nothing records which skill a project used. Argus's own are `research`, `rl`, `argus-http`,
`software` (a change that builds, whose tests ran, and that does what was asked),
`argus-maintenance` (changing this core) and `learning`, on writing a skill worth keeping: what an
agent can run or recheck, proven on a real case.

The web cockpit shows the library: its skills, and as its learning feed the files of
`$ARGUS_HOME/skills/`, newest first, so a skill a role writes appears while the work goes on.

## The web cockpit

`argus` serves the cockpit (on 127.0.0.1, or on `--host`, `0.0.0.0` for every network; project
requests always need a bearer token, `ARGUS_WEB_TOKEN` or one made on the first start, kept in
the state root and printed in the address, since the roles' sandbox shares the machine's
network) and runs the work of its projects in its own process, projects side by side. The page
reads files as they are: the snapshot from `project.json` and `usage.jsonl`, events and the
conversation from `events.jsonl` in the cockpit's event vocabulary, results from the workdir's
files created or changed since the project began. The operator may write at any time: the
Manager takes the message up in its next turn, at once unless it is answering a letter from the
team, while the team's work goes on, and what its letter hands on joins that work. The message
is recorded when it arrives, with its request id and attachment metadata. Manager calls take
waiting letters in order, so replies and interrupted messages keep their request identity;
attachment paths accompany the operator text in the Manager's prompt and its brief to the team.
The message's stream follows that turn and ends with the reply. Stop interrupts every call under
way on the project and records it `stopped`: no turn starts again till Start, or the Manager
handing the team new work at the operator's word. A turn goes on next time in the session its
backend reported, whether it ended, was cut off or failed, except that every review starts a
fresh session. When `argus` starts, the projects a shutdown cut off, still `working`, go on. The
live event stream is server-sent events, in both runtimes.

Files attached to a message are uploaded as they are picked, small ones at once and a large one
in parts, into the
workdir's `.argus/attachments/<id>/`, out of the results; the message names their paths and the
Manager reads them, so nothing in Argus reads what they hold. Deleting a project stops its work
and moves its directory to `$ARGUS_HOME/trash/<id>-<time>`, its workdir left as it is; while the
interrupted calls are still recording how they ended, the deletion is refused rather than waited on.
The trash lists what it holds, from each entry's `project.json`, and a restore moves the directory
back unless a project with its id exists.

What the operator sets on a project is in its `project.json`: its workdir, any existing
directory, given when the project is made or later; its name, which the project list and the
snapshot show, an unnamed project named by its request; and the backend, model and reasoning
effort every role's call uses, written by the Python runtime's setting names, a per-role model
refused rather than half applied. A model or effort is the backend's own word for it, passed to
its command line as one argument. An unset model is saved from the backend's report at the end
of a call, and later calls pass that model explicitly. `modelAutomatic` keeps Automatic as the selection inherited by new
projects; explicit selections inherit the model name. Changing the backend or choosing
Automatic clears the model so the next call can report its choice. The model chooser offers
the one set and takes any other by name. A new workdir or backend clears the role sessions, which belong to the
directory and the backend that started them, and resetting the conversation clears the
Manager's. While work is under way, a change is made in the record that work holds, for the
calls after it; a new workdir or backend is refused until the work stops, since the calls under
way work in them. The sidebar's pins, archive and follow-up marks are kept in
`$ARGUS_HOME/browser-state.json`.

The project list previews the first line of the Manager's last letter addressed to the operator,
or the project's recorded error when it failed. It shares the snapshot's incremental record
reading and recorded-call usage summary: a first list read folds each project's history once;
later reads take appended bytes only. Per-project spend and the list's total exclude calls still
running, and an unknown price keeps the total incomplete. Ended task summaries keep their title
and files; their original progress and closing letters stay available in the task reader.

The code workspace is the workdir, read-only: its tree, which the composer's `@` completion lists
too, a file's text or bytes, and what the system `git` says of it when the workdir is a
repository of its own. A path there passes the check a result's does: inside the workdir, not
hidden. An HTML result is served unchanged on a random project/delivery hostname. Identifiers are
32 lowercase hexadecimal characters, regenerated on service restart. For cockpit requests
at `127.0.0.1`, `[::1]`, `localhost`, `0.0.0.0` or `[::]`, the
default is `http://<identifier>.localhost`
on the browser-facing request Host port, sharing the cockpit listener. Bare loopback names
always remain cockpit hosts. Chrome and Firefox resolve these subdomains locally.
On macOS 15 and earlier, Safari/WKWebView may not resolve `.localhost` subdomains; the fix
is in OS frameworks, not the Safari version ([WebKit bug 160504](https://bugs.webkit.org/show_bug.cgi?id=160504)).
The macOS desktop app uses WKWebView, starts the core without `--preview-url`, and currently
has no preview-domain setting. Its local HTML previews have the same restriction. The UI
explains this and links the existing preview URL to a new tab; use Chrome/Firefox there.
Opening the same URL in Safari on an affected OS does not fix DNS resolution. CLI users may
configure a resolvable preview template. No DNS probing or automatic shared-storage fallback
is performed.
`--preview-url` supplies an HTTP(S) origin template with exactly one `*` in the first DNS
label (e.g. `https://js-trial1-*.argusbot.cn`); no credentials, path, query or fragment.
The entire matching namespace, including unknown, empty and non-label identifiers, is
reserved for static previews. Keep the cockpit hostname outside it: for example, use
`*.preview.example.com` with `argus.example.com`, rather than `*.example.com`.
Likewise `argus.localhost` is reserved by the default `*.localhost` template. Unknown or
expired preview links (identifiers end when Argus restarts) return a plain-text 404 saying
so, never cockpit pages or API.
Assigned hosts serve only the identifier's workspace/delivery files. Absolute asset paths start
at the workspace root. Project/delivery subdomains separate
localStorage, IndexedDB and caches; iframe storage may be partitioned from top-level storage.
Local `.localhost` iframes are cross-site with the cockpit: Chromium restricts default/Lax
cookies (explicit `SameSite=None; Secure` may work), while WebKit can block all third-party
cookies. Check cookie-dependent websites using the UI's new-tab link. The same-site LAN
fallback and same-site hosted templates avoid this particular restriction; other configured
cross-site domains remain subject to the browser's cookie policy.
Without a template, LAN/mobile requests retain the independent static listener at the server's
cockpit port + 1 and a keyed path. This fallback separates cockpit localStorage but **shares
preview storage between projects/deliveries**; cookies do not separate by port. Forward that
second port for remote access, or configure a wildcard template for full storage isolation.
The fallback port is bound on the first LAN preview request. If listening fails, the request
returns 503 `preview_unavailable` naming the address and the reason, and the failed listener
state is released. After
freeing the port, a new preview request can bind it; loopback previews keep working.
API Origin rejection uses the same preview hostname matcher and rejects an active fallback port.
Requests without Host, or with an empty one, use the socket's local address and port; malformed
Host headers return a readable 400. Valid IPv4-mapped IPv6 Host headers are accepted.
Proxies must preserve Host and Origin. Hosted deployment additionally needs wildcard DNS/TLS,
Host-based tenant routing for preview GET/HEAD without cookies or bearer credentials, and
`__Host-` portal cookies (only accept that cookie name) against sibling-domain cookie injection.
A proxy replacing Host with localhost is indistinguishable from local access: set the template
at tenant startup. If it strips Origin, its own origin check must enforce the API boundary.
Website downloads contain the page and files that preview has served, removing their common
parent directories while preserving relative paths. Results chosen together download in a
separate zip. A task
opened from the list is its card on the map with every letter written about it and the error of
its last call; the search reads each project's request,
conversation and letters as they are, with no index. The machine's CPUs and memory (from Node)
and NVIDIA GPUs (from `nvidia-smi`) are read when the page asks, never polled.

A text result is edited in the page as editions saved beside it, never over it, each save
refused after one it never saw; an answer's revisions are kept the same way. Each text file of a
result the Manager delivers is copied as it was, so the same file delivered again shows, as a
unified diff of its lines, what changed since.

The page offers only what the server declares in `/api/meta`. The core declares the required
capabilities, `project.attachments.v1`, `system.resources.v1`, `project.workdir.v1`,
`project.config.v1` and `project.editions.v1`; the settings omit the advisor (`advisor.config.v1`)
and the light model, which the core's settings do not list. The Python server declares every capability.

Browser notifications are off by default. The operator can enable them in settings for that
browser; only that action requests permission. The open project's pending question or delivery
receipt supplies the reminder, through the same state stream as the page. The browser remembers
which questions and deliveries it notified, so reading the same state again does not repeat one.
Tabs for the same site share the preference and sending records. Native Web Locks claim each
reminder without waiting for another tab; browsers without them leave notifications unavailable.
Notifications require the page to remain open and the browser to support and permit them.

The progress map reads the same files: a card per task, its brief the letter that opened it, and
on it a step per letter written about it, by whom to whom. A task opened in the reader lists its
calls in order, each with the letter it ended with and the path of its raw output
(`calls/<call>.jsonl`). Arrows show the tasks' recorded `after` dependencies, and tasks at the same dependency depth
share a column. Time order adds no arrows, and the map generates no written explanations.
The map envelope contains only `id`, `tasks`, `events` and `cursor`; its events belong only to
cards currently drawn. It sends short headings for progress and historical letters, with the recorded event
identity, order and participants kept for drawing. The shared reading prepares those headings
once as new events arrive. Opening a task reads its full objective and latest words from the
backlog detail endpoint; selecting a historical letter uses the same endpoint with `letter=<id>`,
whose `notes` contains that original letter. Request cards read their complete answer from the
detail endpoint, too.

## The agent call

The only boundary between the flow and the backends:

```ts
callAgent(request: AgentRequest): Promise<AgentResult>
```

(`packages/core/src/types.ts`). Each role's session runs on one CLI: Codex's `app-server` (a
thread, each turn a `turn/start` with the form's schema as its `outputSchema`) and Claude Code's
`-p --input-format stream-json` (each turn a user message, the schema given by `--json-schema`)
stay open from the session's first turn to its end, so what a turn leaves running in the
background runs on into the next. Review sessions end with each call and are never resumed.
Copilot and Pi take one prompt on stdin and exit with the turn (Copilot's ACP mode, which could
stay open, offered the model neither an extension's tool nor an MCP server's, so the hand-off
had no way in). The agent layer reads the CLI's JSON events, takes only the backend's own
completion event as success (Codex `turn/completed` with status `completed`, Claude `result`
with `subtype: success`, Copilot `result` with `exitCode: 0`, Pi `agent_settled` after a last
answer that did not fail), and judges the hand-off the turn ended with: Codex's last message,
Claude's `structured_output`, or the `hand_off` call in the last message of Copilot's main agent
or of Pi. The shape is already the strict one OpenAI's structured outputs take: both fields
required, no other allowed.

Copilot and Pi have no final answer held to a schema, so each call writes an extension that
registers `hand_off` with the form's schema: for Copilot a plugin (`--plugin-dir`) whose tool is
`isTerminal`, its experimental extensions enabled for the run by
`COPILOT_CLI_ENABLED_FEATURE_FLAGS=EXTENSIONS` (`--experimental` would turn them on in the
operator's settings for good); for Pi `--extension` with a tool returning `terminate: true`. Pi
checks the call against the schema, so the model corrects a malformed form inside its turn;
Copilot does not, and Argus's judgment is the check.

Usage: Codex from the session's rollout file (`token_usage_record`, per request); Claude from
`result.usage` (input counts include cache reads and writes), its cost what `total_cost_usd`, the
CLI's cost so far, grew by;
Copilot from the session's event log (`session-state/<id>/events.jsonl`, a
`session.usage_record` per request, subagents' included), its AI credits as the cost ($0.01
each); Pi from each assistant message's `usage`, with `cost.total` as the cost.

The call ledger, `usage.jsonl`, records each call's end time (`ts`, epoch seconds),
role, model, token counts, nullable cost and elapsed `ms`. The shared record reader folds
complete appended lines and retains one summary until more usage arrives. The sidebar's
project row and the snapshot's `usage_summary` share its recorded totals; the snapshot
extends the duration with live calls. Its `by_role`,
`by_model` and `by_date` arrays partition the calls (a missing identity is `null`);
dates are UTC. Each group carries the same counts, known cost and pricing completeness
as the total. An unknown price remains unknown; zero is a reported price.

`usage_summary.work_duration_ms` is the union of intervals `[ts * 1000 - ms, ts * 1000]`.
Parallel calls count once and gaps count zero. Completed calls contribute through the
usage ledger; each live call also contributes
`[started_ts * 1000, read_at]` at snapshot time. Completed calls without usage, and older
lines without a duration or valid timestamp, add no time. `calls_without_duration` counts
the latter, so the page can mark the duration as a lower bound. The header reuses the
elapsed clock to extend the union after `read_at` while live calls remain; a new snapshot replaces this reading when calls start or end.

A Manager call owns one reply row. Its `answers` names the operator request;
team letters in `forManager` carry an `input_id` that their calls and handoffs
retain across recovery and form correction. Each input owns a reply and its
position beside the incoming letter, including when another input is handled
before it resumes. Legacy pending strings are associated with their unaccepted
incoming letters and original calls before they receive an input identity. Migration
and projection share the input fold. A completed first attempt with a session
retains its input for one form correction before taking queued letters. Message
steps accumulate in the reply's `steps` string array,
and the handoff supplies `text` and its addressee. Non-request turns retain their
process; their running/stopped state is expressed by the project status bar.
Planner calls record `planner_inputs`, the count of inbox letters actually read.
The projection consumes that prefix only at the accepted handoff, once even for
multiple outgoing letters; form corrections keep the original read count. A batch
whose letters all trace to one reply attaches its process there. Mixed or unknown
sources have an independent process row; newly opened tasks inherit that row for
later Planner turns, while existing tasks retain their originating association.
Older records consider pending recorded sources at call start and consume them at
the accepted letter; ambiguous batches stay independent. Role logs retain each
recorded step separately in record order.
A call retains its reply association until its accepted handoff is recorded, including
failure recovery and form correction. Reply positions and steps survive completion,
interruption, paging and reload; a reply containing only steps remains visible.
Live words carry their kind: process words append to `steps`, while form letter
words supply `text`. A handoff to the Planner or Engineer folds its letter body;
the reply heading and process questions remain visible. Search opens that body.

Conversation and map task cards show only the
first file in the Manager's delivery for the request that opened their work; they
show no result before that delivery. The task detail reader retains the team's
full file selections and original letters.

Conversation task rows expand on demand through `/api/projects/{id}/backlog/{task}`.
The detail's `notes` contains the recorded letters and their addressees, including review
returns and operator answers. `changed_files` contains the recorded edit paths from the
latest Engineer turn, reset when the next Engineer call starts; it describes recorded
edits, not a filesystem diff. The detail's `result_paths` includes files explicitly
selected in task letters, including results awaiting review; card results wait for
closure and the Manager's primary selection. The page uses the detail's paths for the file viewer and the first result image,
and displays the original letters without inferring a verdict from their text.

Sandbox: on Linux with a working `bwrap`, the CLI runs under bubblewrap: system directories
read-only, the workdir and the task's write directories writable for the Engineer (the Manager, Planner and
Reviewer read it only), a private home per workdir
with the CLI's login file passed through, and the CLI's login environment plus the operator's
registered environment values. Each review has a fresh disk-backed temporary directory under
`$ARGUS_HOME/sandbox`, bound to `/tmp` under bubblewrap or selected by `TMPDIR` without it,
and removed when the review ends. Other roles' sandboxes cannot see that directory.
Other roles retain their existing temporary filesystem. Its
`~/.cache` is one every project's roles share (`$ARGUS_HOME/cache`, npm's cache in it), so a
model, package or browser one project downloads the next finds; the machine's own cache, which
may hold tokens, stays out. Codex runs there without plugins: the private home has none of the
operator's, and the catalog it would clone is 100MB a workdir. Without `bwrap` (macOS, Windows)
the CLI's own sandbox mode is used: Codex's `workspace-write`, on Windows its sandbox that needs
no administrator (`windows.sandbox="unelevated"`); Claude Code's `acceptEdits`, edits without
commands.

Settings registers environment names and literal string values for all projects in
`$ARGUS_HOME/credentials.json`, a single JSON object in a file with mode 600. A newly started
backend process receives these registrations over its inherited environment; the backend's
existing login exceptions still apply, and other sensitive variables remain filtered. The
authenticated `/api/settings/credentials` route reads only names, writes one name and value
with PUT, and deletes a name with DELETE on `/api/settings/credentials/<name>`. Settings clears
the inputs after saving and whenever it closes; saved values are never sent back to the page.

Starting the CLI: no shell is involved. On Windows npm installs a CLI as a `.cmd` shim, which
only `cmd.exe` runs; the agent layer runs the file the shim names instead, a script with
Node.

A session ends, and everything its CLI started with it, when its task closes, its project
stops or comes to rest with no question or failure waiting in it, Argus exits, a turn on it is
stopped, or the CLI exits on its own; the next turn starts a new CLI, which resumes the session
from the backend's record. A turn that asks for other settings (a model, an effort) does the
same. A letter the Manager or Planner writes to a task under way waits on the task till a turn that read it
ends; Codex also puts it into the turn under way (`turn/steer`), so the holder reads it as they
go. Ending a CLI ends what it
started however it detached (`nohup … &`, `setsid`): the CLI's environment carries
`ARGUS_TURN`, a value of its own that whatever it starts inherits, and the runtime ends what
carries it or stays in the CLI's process group (`executeProcess`); under bubblewrap the sandbox
ends with the CLI. On Windows a process cannot read another's environment and Node has no job
object, so ending reaches the CLI's process tree (`taskkill /T`) only while the CLI runs.

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
