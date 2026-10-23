# Role sessions and on-demand Skills

## Sessions

The backend decides how a role session is kept. Resumable native CLIs (Pi,
Codex, Claude/Qoder, Copilot, OpenCode, and Grok) resume each role's provider
thread (`rolling`); fresh-only runners such as DeepSeek Harness start a `fresh`
session per role turn. There is no policy to configure.

The backend owns its context window and compacts it itself, so Argus never
rotates a session for its turn count or size. Only a branch, objective revision,
backend, or model change, or an explicit Reviewer signal, starts a new thread.
Backend resume failure drops that role's thread and retries from durable state.
This covers Planner, Engineer, and Reviewer autonomous turns; the persistent
Manager follows the same rule.

With a resumable backend, each role has a separate JSON capsule under the
mission state directory's `role-sessions/`. A capsule contains only the objective
revision, repository map, referenced/inspected paths, latest decisive output,
open checkpoint items, checkpoint pointer, provider thread id, and counters. It
never contains a transcript or another role's private context. Capsules are
runtime-owned and atomically replaced. A fresh-only backend reads the same capsule
and checkpoint paths without needing provider resume support. Mission context
initializes an empty checkpoint placeholder atomically, without overwriting later
role-authored state. The checkpoint remains optional recovery metadata: a missing,
concurrently deleted, unreadable, or unwritable checkpoint/capsule emits a
persistence warning and never changes an otherwise successful Engineer, Reviewer,
or Planner result.

Every role call emits `role.session.turn` with policy, fresh/resumed/rotated
action, rotation reason, prompt size, token usage, wall time, and capsule path.
Correlate these events with `agent.io.*` file-tool events and
`round.review.completed` to compare repeated reads, repository remapping,
Reviewer acceptance, and correctness on matched task replays. The focused test
`tests/test_role_session_lifecycle.py` verifies fresh and resumed sessions,
restart recovery, role isolation, and lower Reviewer prompt bytes with an
unchanged verdict.

## Skill discovery contract

The runtime gives each role ordered paths, never selected Skill bodies:

1. project library;
2. active vertical/domain library;
3. shared global library.

Within each layer, the role's OWN directory has priority. Cross-role directories
are REFERENCE-only. An Agent searches filenames/frontmatter when reusable prior
knowledge is likely to help and opens a body only after its description is a
clear fit. A wrong Skill is worse than no Skill. Current task authority and fresh
evidence always override Skill text.

Pi receives the role-owned paths through its explicit `--skill` loader while
ambient Pi Skills stay disabled. Codex, Claude, Copilot, Cursor, and OpenCode receive the
same portable path contract in the role prompt because their native discovery
locations/APIs are not interchangeable with Argus state roots. Newly written
Markdown is therefore discoverable from the stable root immediately, without a
prompt rebuild or daemon restart.

`skill.library.available` records role, ordered roots, OWN paths, REFERENCE paths,
and discovery mode. Provider `agent.io.*` events retain actual on-demand file
access for offline useful/false-reuse evaluation; the harness does not introduce
a matcher or scorer.

### Verus specification generation and repair

The Verus module workflow (a shared contract plus one Skill per task role, in
English and Chinese) no longer ships in the built-in software vertical: a
usage audit of 121 projects found it never opened, and its tooling is not part
of this repository. The files remain in git history (last shipped at the
commit before this section changed) so they can be published as a Vertical
Store plugin; the software role playbooks no longer link to them.

## Reviewer and Observer access

The Reviewer and the Observer run with the Engineer's access on every backend:
the same approval mode, sandbox and tool set, so a Reviewer can rerun checks
and reach the network itself. Outside safe mode the CLI asks no permission;
in safe mode it uses the Engineer's full-auto setting. Either way the agent
sandbox shows it only its workdir and the directories it is handed. The
Reviewer's verdict tools ride on top as the only addition (Claude and Qoder name them in `--allowedTools`, which no permission
mode pre-approves).

Independence rests on facts, not permissions. The round already snapshots the
workspace as `candidate` before the Reviewer starts; after the Reviewer's turn
the host snapshots it again and compares. Any changed path is recorded as
`changed_during_review` on `round.review.completed`, beside a verdict that
stands as written, and the Manager is consulted once through its supervision
to decide whether the review counts. A snapshot or comparison that fails is
skipped and never holds up the review. The specialist final-paper passes run
before the `candidate` snapshot and are not covered by this check.

If the provider call has already returned, its token/premium usage and session
metadata are retained on a blocked review rather than discarded with the
verdict. The same applies to report or stage-state persistence failures during
review finalization.
