# Role sessions and on-demand Skills

## Session experiment

Production uses a backend-aware default on every supported platform:

```text
ARGUS_SKILL_ROLE_SESSION_POLICY=auto
```

`auto` selects bounded `rolling` sessions for resumable native CLIs (Pi, Codex,
Claude/Qoder, Copilot, OpenCode, and Grok) and remains `fresh` for fresh-only
runners such as DeepSeek Harness. This is an Argus runtime default, not a
machine-local Pi setting.

The same mission can also be run with explicit policies:

- `fresh`: one provider session per role turn;
- `mission`: one isolated provider session per mission and role;
- `rolling`: resume per mission and role, then rotate at six turns or 120,000
  observed input tokens by default.

The rolling limits are configurable with
`ARGUS_SKILL_ROLE_SESSION_MAX_TURNS` and
`ARGUS_SKILL_ROLE_SESSION_MAX_INPUT_TOKENS`. A branch, objective revision,
backend, or model change also rotates the session. Backend resume failure drops
that role's thread and retries from durable state. Manager conversation sessions
keep their existing daemon-generation lifecycle; this experiment covers Planner,
Engineer, and Reviewer autonomous turns.

With `mission` or `rolling`, each role has a separate JSON capsule under the
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
`tests/test_role_session_lifecycle.py` verifies all three policies, restart
recovery, role isolation, bounded rotation, and lower Reviewer prompt bytes with
an unchanged verdict.

Rollback is immediate: set the policy to `fresh`. Existing capsules are ignored
and may be deleted after no old daemon uses them.

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
