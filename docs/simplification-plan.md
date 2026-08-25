# Simplification Plan

A plan to make Argus smaller. It is a weight-loss problem, not a rewrite: the
system works, and the goal is to remove what is not carrying weight.

Companion documents: **[system audit](system-audit.md)** for the measurements this
plan acts on, **[failure modes](failure-modes-and-fixes.md)** for the behaviour it
is meant to change.

Chinese version: [simplification-plan.zh-CN.md](simplification-plan.zh-CN.md)

---

## The rule

> **Never fix a bug by adding a mechanism.** Fix the cause or delete the code.

Everything below follows from it. This plan was reviewed by a second model from a
different lab; where its reading of the tree differed from ours, we re-measured
and the corrected numbers are the ones used here.

## The core finding

The expensive thing is not any single gate. It is a loop:

> **gates produce artifacts → artifacts produce repair state → repair state is
> injected back into the prompt → retries spin.**

`skills/research_gates.py` is that loop in one file: a single advisory check
writes up to four artifacts per run (`RESULT.json`, `REVIEW.md`,
`FAILURES.json`, `REPAIR_TASKS.md`), and `REPAIR_TASKS.md` is a set of
instructions written back into the next round. Delete the loop and let the
read-only Reviewer judge real evidence instead.

---

## Ordered plan

Work top to bottom. Each step is verifiable before the next begins.

### 1. Delete the no-op Wiki lifecycle API — provably safe

`wiki/lifecycle.py:54` `maintain_wikis_after_mission()` takes seven parameters,
discards five, and says so: `"""Do nothing: Agents maintain pages and INDEX.md
during the mission."""` It has no callers.

Keep `ensure_project_wiki()` at `:22`.
**Verify:** repo-wide reference search; `pytest tests/test_wiki_bootstrap.py tests/test_minimal_skill_wiki.py`.

### 2. Delete the 31 unreferenced event types

Of 129 `EventType` members, **31 are never referenced by symbol or by string
value**; 20 of those are `SKILL_*` and `WIKI_*` — the two knowledge surfaces the
system is supposed to learn through. Remove them together with their payload
schemas, frontend catalog entries, and generated types.

Handle separately: **6 events are emitted by raw string literal rather than
through the enum** (`LIFE_MISSION_SKIPPED`, `LIFE_MISSION_REQUEUED`,
`LIFE_VERTICAL_RESOLVED`, `LIFE_INBOX_QUEUED`, `SKILL_OUTCOME`,
`OPERATOR_ALERT`). These are not dead. Route them through the enum so the catalog
becomes a reliable index again.

**Risk:** external consumers of old event logs. **Verify:** replay an old
`events.jsonl`; `pytest tests/core/test_event_catalog.py`.

### 3. Cut the unconditional rendering block — best value per line removed

`manager_rendering_prompt` contributes **2,923 characters** to *every* stage
decision in *every* vertical, whether or not the decision concerns rendering
(appended at `manager/_stage_ops.py:779`). It is larger than the median vertical
banner (1,318 chars) and nearly as large as the decision prompt it accompanies.

This is the only prompt cost every vertical pays. Make it conditional on the
decision actually involving presentation, or delete it.

**Verify:** stage decisions still produce valid verdicts across two verticals.

### 4. Remove prose-regex ownership escalation

`core/role_handoff.py:12-79` decides who owns a decision with a regex over prose
containing `access`, `release`, `production`, `delete`, `pay`. A summary that
says "delete the temporary directory" routes to a human.

Delete `_OPERATOR_AUTHORITY_RE`, `_REVIEW_ACTION_RE`, and
`_runtime_owned_review_request()`. Make `NEXT_OWNER` authoritative: an explicit
`reviewer` means reviewer regardless of vocabulary; a legacy `OPERATOR_QUESTION`
with no owner stays with the operator.

Real authority checks at irreversible-action boundaries stay. A word list is not
an authority boundary.

**Verify:** `pytest tests/test_structured_decisions_are_not_reparsed.py`, plus
handoffs whose text contains "release", "production", and "delete".

### 5. Delete the gate/repair ecology — highest behavioural value

Delete the advisory literature, theory, numerical, novelty, novelty-seeking,
paper-type, and manuscript-package gates, then their shared
`skills/research_gates.py`, `physics/gate_feedback.py`, the capability trace, the
generated repair artifacts, and the role-banner injection that carries them
(`physics/stages.py`).

`physics/gates/novelty_seeking.py:40` is the clearest case: ten directions,
eleven reasoning columns, six scores, four supporting files — **170 table cells
to earn the right to make a claim** — and it never evaluates novelty. It counts
rows.

Keep only outcome checks on a real requested deliverable — "the paper compiles"
when a paper was requested. Delete exact CSV headers, figure and reference
counts, wording bans, and section quotas.

This directly restores the posture already written into the kernel Reviewer:
*"Ignore GROUND_TRUTH/gate/marker/status/provenance files … and artifact hygiene
— the scorer's number is the only evidence."*

**Verify:** on completed physics missions, compare Reviewer verdict quality,
build success, and claim accuracy — **not** gate-pass rate.

### 6. Stop re-litigating clean Reviewer acceptances

Manager remains the sole stage-state writer, but should not be invoked to
re-decide a Reviewer acceptance that raised no exception. Reserve it for strategy
changes, conflicts, rollback, terminal completion, and authority.

This is the intended division doing its job: Manager strategy, Planner
decomposition, Engineer local iteration, Reviewer independent judgement.

### 7. Make daemon self-maintenance an ordinary mission

`ARGUS_SKILL_SELF_MAINTENANCE` defaults to `"1"`
(`daemon/_life_worker_run.py:46`), enabling a 3,186-line worktree/canary/
publication subsystem. Argus changes can be ordinary engineering missions under
the normal Engineer/Reviewer loop.

Run with it disabled first. **This is the largest deletion by line count and the
smallest by behavioural improvement** — do it after step 5, not before.

**Risk:** loses unattended framework self-updating. That is a real capability;
decide deliberately.

### 8. Only then purge defensive handlers and spin loops

Do this last. Cleaning exception handling inside subsystems you are about to
delete is wasted work.

---

## A mechanical rule for the 2,277 `try:` blocks

Do not review them case by case. Keep a handler **only** when all four hold:

1. it surrounds one external boundary — network, subprocess, optional telemetry, cleanup;
2. it catches expected failures of that boundary, or is the single top-level mission boundary;
3. it returns an explicit non-success — `blocked`, a retryable failure, unchanged state — or affects only optional observability;
4. it records the failure and has one bounded retry owner.

**Delete or propagate every handler that returns a success-shaped default**
(`""`, `[]`, `{}`, `False`, `pass`, `continue`) for state, prompts, routing,
checklists, authority, evidence, locks, budgets, or stage transitions.

| | Example |
| --- | --- |
| **Good** | `reviewer/_core.py` catches backend failure and returns `blocked` |
| **Bad** | `skills/stage_machine.py` silently substitutes an empty checklist |

A runtime that cannot fail is a runtime that cannot tell you it is broken.

---

## What not to delete

- **The independent read-only Reviewer.** `require_independent_review` defaults to
  `True` (`engineer/round_config.py`) and the Reviewer runs read-only. This is the
  load-bearing wall that makes unattended operation possible.
- One canonical stage writer and atomic stage state.
- Durable event and backlog history.
- **Real** authority checks at irreversible-action boundaries — as opposed to
  regexes over summaries.
- Provider failure-to-block behaviour, cost accounting, GPU leases.
- The tolerant prose parsing in `core/role_reply.py`. It already makes this
  document's argument: *"The harness is not smarter than the agent, and demanding
  a wire format is the harness deciding how the agent may speak."*

---

## The trap

The likeliest way this effort fails is by replacing what it deletes with a
"unified gate system", a "unified event system", or a "resilience layer".

`research_gates.py` **is already that mistake**: one advisory check grew into four
artifacts, a stall tracker, repair prompts, and extra control flow.

Use the owners that already exist. Delete callers and formats rather than
consolidating them. **If a simplification exposes a failure, surface it to the
Reviewer — do not add a layer.**
