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

### Verus specification generation and repair

The software vertical includes one
[shared Verus module workflow](../argus/verticals/software/skills/verus-spec-generation-and-repair.md)
and an OWN-pool Skill for every task role:

| Role | Own Skill and responsibility |
|---|---|
| Manager | [Scope and delivery](../argus/verticals/software/skills/manager/formal-verification/verus-spec-generation-and-repair.md): preserve the requested module and require both proof tracks and all deliverables |
| Planner | [Submodule and proof planning](../argus/verticals/software/skills/planner/formal-verification/verus-spec-generation-and-repair.md): analyze submodules first, schedule views before dependent API specs, then order API work bottom-up or justify another order |
| Engineer | [API specification and repair](../argus/verticals/software/skills/engineer/formal-verification/verus-spec-generation-and-repair.md): read source and docstrings, implement views/specs, and iterate both proof tracks |
| Reviewer | [Independent proof review](../argus/verticals/software/skills/reviewer/formal-verification/verus-spec-generation-and-repair.md): verify both dimensions and reconcile full-module/per-API coverage |

The shared contract is a direct general Skill, visible in every role's OWN
paths. Role-specific instructions no longer rely on consulting the Engineer's
REFERENCE pool. Front-door/SELF uses the Manager scope requirements. Existing
software role playbooks link to their respective Verus Skills; no runtime
keyword matcher, body injection, or scheduler stage was added.

For these tasks, correctness means a source implementation proof verified by
Verus, and completeness means a proof/check through `spec-determin-tool`.
Typechecking, source review, native tests, and client proofs do not substitute
for either requirement. Failed or inconclusive results feed repair; accept an
API only when both dimensions pass for the same candidate.

Implementation changes must first be raised as issues and receive strict
independent review, including executable rewrites in proof copies. Canonical
Rust/std or Verus changes also need explicit operator authorization. Demonstrated
Verus limitations (such as unsupported Rust syntax) and independently confirmed
std implementation/docstring defects must be reported as issues; proof failure
or `UNKNOWN` alone is not evidence of an upstream defect.

Minimum deliverables are specs organized under `specs/<submodule>/` with a
module aggregate, per-API entries under `proofs/correctness/`, separate entries
under `proofs/completeness/`, and `issues/INDEX.md` plus issue reports.
Retain replay inputs/commands and a compact API-to-proof/issue index.
Repository-native equivalent layouts must map explicitly to all four
collections. With no discovered issues, record that and the reviewed scope.
Local issue records are mandatory; external filing follows Manager's configured
target and operator authorization. Record real URLs only after filing, and
keep unfiled drafts explicitly pending rather than claiming upstream publication.

The [shared Chinese review and role index](../argus/verticals/software/skills/references/verus-spec-generation-and-repair.zh-CN.md)
links all four role translations. Software-context seeding/export copies them
as `references/` assets, not additional independently enumerated Skills.
The fixed verifier baseline and existing `old`/`final` borrowing guidance remain
in the shared contract. Project-specific paths, versions, counts, and progress
remain in the project.

## Read-only Reviewer validation

Copilot Reviewers retain their read-only tool list. Skill-library roots are
available as scoped read paths. Operators can additionally set
`ARGUS_SKILL_REVIEWER_READ_DIRS` to a JSON array of existing absolute directories
for authoritative source, tool, and runtime inputs. This does not enable native
shell or write tools.

Setting `ARGUS_SKILL_REVIEWER_VALIDATION_IMAGE` to an already-installed local
Docker image enables the call-bound `run_review_command` tool. It executes an
argv array inside a Linux container with a read-only root filesystem,
read-only project/input mounts, no network, dropped capabilities, and a
separate writable scratch directory. Use `{scratch}` in command arguments for
compiler/checker output paths. Required host toolchains and their runtime
libraries must be included explicitly in the read-directory configuration.
No credentials or review-bridge tokens are passed into the container or the
Docker daemon client. Argus resolves the selected local Unix-socket endpoint
before switching to a private, empty Docker client configuration. This preserves
the operator's selected context without inheriting configured proxies, registry
credentials, credential helpers, TLS settings, or client headers. Images are
resolved to their installed immutable identity and are never pulled.

The native tools use short requests even when a command takes longer than the
bridge's transport deadline:

| Tool | Use |
| --- | --- |
| `run_review_command` | Start an argv command and wait at most five seconds for its result. |
| `get_review_command` | Query the returned `command_id`, waiting at most five seconds. |
| `cancel_review_command` | Request cancellation of that command and wait at most five seconds. |

A command that is still running must be queried, not resubmitted or treated as
complete. Command identifiers belong only to the current Reviewer turn; another
turn cannot query or cancel them through these tools. A zero or omitted command
timeout permits execution until completion or the end of this turn, not beyond
it. Explicit timeouts cover command execution; Docker setup has its own bounded
waits. Native tool transports and permissions are unchanged.

Command admission happens before filesystem preparation. Slow directory or
receipt initialization remains owned by the current turn and can return
`running` before log files exist. Output is empty only until those files are
prepared; later read errors remain errors. A pending worker never reports a
terminal status merely because its in-memory receipt has advanced ahead of
cleanup or persistence.

`cancel_review_command` uses the bridge's reserved cancellation capacity rather
than competing for ordinary operation slots. Authentication, turn ownership,
and the overall handler limit still apply.

The host writes a command receipt before Docker setup and retains it with full
stdout and stderr under the Argus profile's `reviewer-checks/` area. Expected
missing-tool, context, image, and daemon failures return a persistent, redacted
environment diagnosis. Unexpected internal exceptions remain generic. None of
these results is a proof conclusion, and missing tools, denied source writes,
or unsupported inputs never cause unsandboxed execution.

Ending the Reviewer turn cancels active commands and waits for cleanup, including
when the provider raises or exits without a native review action. Docker context
and image queries have ten-second deadlines; container creation has a separate
thirty-second deadline. Forced removal has a five-second deadline.
Interrupted client process groups are killed and given at most two seconds to
be reaped. Teardown shares a forty-second wait across all active commands,
including their filesystem preparation. An initializer that cannot finish
within this deadline blocks approval even if its request already timed out.
Container creation and starting are separate so cancellation during creation
cannot subsequently launch the command.

The receipt preserves a timeout or cancellation even if removal also fails;
cleanup is recorded separately. Unconfirmed creation, failed removal, or failed
client termination makes teardown report an infrastructure failure rather than
silently leave work behind. The host retains execution and cleanup state in
memory and observes worker failures during teardown, even when an earlier tool
request already reported the failure. A receipt write or replacement failure
also invalidates an earlier native approval, including when removal fails at
the same time. An older receipt still saying the command is running cannot
override those failures, and a later successful write does not erase them.
An unresponsive daemon or a hard-killed host can
still require the operator to inspect and remove the exact named container on
the recorded local endpoint. This mechanism does not promise recovery after
host termination.

Validation execution does not submit a review decision. The Reviewer must
still use its native approve/revise/defer/decision/replan action; prose and
printed command output cannot forge acceptance. Launch workers with the intended
Python environment so their MCP subprocesses can import the installed `mcp`
dependency.

Validation cleanup or receipt failures still block approval. If the provider
call has already returned, its token/premium usage and session metadata are
retained on the blocked review rather than discarded with the verdict.
The same applies to temporary review-report cleanup and report or stage-state
persistence failures during review finalization.
