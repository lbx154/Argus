# Bug Report: Scope-stage livelock — `planner_wait_advance_rejected` blocks all forward progress

## Summary (TL;DR)

In a continuous (`--continuous`) open-ended mission, the pipeline gets **permanently stuck in
the first stage (`scope`)** and never advances. Every cycle the Reviewer **certifies the
scope-stage evidence as accepted**, yet the Manager's stage-transition decision immediately
returns `hold` with diagnostic `planner_wait_advance_rejected` / reason *"planner waiting cannot
advance without reviewer evidence"*. The Planner then re-issues yet another scope-certification
task, the Reviewer accepts it again, the Manager rejects advancement again — an infinite
**livelock** (not a hard deadlock: all agents stay busy) that burns budget indefinitely while
producing zero deliverable work.

- **Impact:** ~40 min wall-clock, **~$19.6 budget wasted** and climbing, `BEST = —`, the target
  file (`rmsnorm_candidate.py`) never edited. 0% forward progress.
- **Severity:** High — any continuous mission on this vertical can silently burn unbounded budget.

## Environment

| Field | Value |
|---|---|
| Repo | `argus-skill` (editable install) |
| Commit | `61d95dc060e236ac30e8c244b06fdc3c5b8e9b5f` |
| Commit date | 2026-07-20 07:08:46 +0000 |
| Commit subject | `feat: reconcile stale daemons on launch` |
| webapi protocol | `argus.webapi/1.11` (`API_PROTOCOL_MINOR = 11`) |
| Launch mode | `argus-skill --continuous --objective "..."` (foreground cockpit) |
| Vertical (resolved) | `kernel_engineering`, kind=`optimize`, workflow=direct |
| Stage pipeline | scope → environment → baseline → optimize → validate → report |
| Session id | `s-b7783501` |
| Regression note | The **previous** commit (before this session's `git pull`) advanced through
scope normally on the same task. The livelock appeared only after upgrading to `61d95dc`. |

## Symptom / Observed behavior

The mission never leaves `scope`. Cockpit shows `STAGE Scope` frozen while budget rises.

### Evidence 1 — every `stage_decision` is an identical rejection

All 7 recorded `life.manager.stage_decision` events are byte-for-byte identical:

```json
{"current_stage": "scope", "target_stage": "scope", "action": "hold",
 "trigger": "planner_waiting_reconciliation", "source": "manager_llm",
 "resolves_wait": false, "diagnostic": "planner_wait_advance_rejected",
 "reason": "planner waiting cannot advance without reviewer evidence"}
```

- `action`: `hold` × 7 (never `advance`)
- `target_stage`: `scope` × 7 (never points forward)
- `resolves_wait`: `false` × 7 (the planner's wait is never resolved)

### Evidence 2 — the Planner only ever issues scope-certification tasks

All 8 `life.planner.task_added` are variants of the same self-certifying meta-task; **none**
implements or benchmarks the kernel:

```
07:25:32  Complete RMSNorm scope-stage evidence artifacts
07:32:25  Certify scope-stage closure
07:36:42  Re-certify scope-stage closure
07:41:51  Complete scope-stage Reviewer certification
07:47:20  Complete scope-stage Reviewer evidence
07:51:54  Produce complete scope-stage Reviewer certification
07:57:01  Produce manager-consumable scope-stage certification   <- Planner senses the gap
08:02:15  Produce complete scope-stage Reviewer certification
```

### Evidence 3 — the contradiction: evidence is ACCEPTED, then immediately declared MISSING

Interleaved mission-completion vs stage-decision timeline. Each certification mission completes
with **"Evidence accepted"**, and within ~1 minute the Manager says advancement is impossible for
lack of reviewer evidence:

```
07:41:51  start  Complete scope-stage Reviewer certification
07:45:02  ✓done  Complete scope-stage Reviewer certification        (Evidence accepted)
07:46:19  STAGE! planner waiting cannot advance without reviewer evidence   <-- 1 min later
07:47:20  start  Complete scope-stage Reviewer evidence
07:50:03  ✓done  Complete scope-stage Reviewer evidence             (Evidence accepted)
07:51:02  STAGE! planner waiting cannot advance without reviewer evidence
07:51:54  start  Produce complete scope-stage Reviewer certification
07:54:26  ✓done  Produce complete scope-stage Reviewer certification (Evidence accepted)
07:55:43  STAGE! planner waiting cannot advance without reviewer evidence
07:57:01  start  Produce manager-consumable scope-stage certification
08:00:19  ✓done  Produce manager-consumable scope-stage certification(Evidence accepted)
08:01:16  STAGE! planner waiting cannot advance without reviewer evidence
08:03:24  STAGE! planner waiting cannot advance without reviewer evidence
```

The event stream also contains **6 × `research.achievement.certified`** events — i.e. the
scope achievements are genuinely being certified — yet the stage gate never consumes them.

## Root-cause analysis

Two code sites interact to form the trap.

### Site A — trigger condition: `argus_skill/manager/_core.py:1624`

```python
planner_wait_reconciliation = bool(
    open_ended
    and review is None                       # no reviewer object passed into this decision
    and planner_verdict is not None
    and bool(getattr(planner_verdict, "waiting", False))
    and not bool(getattr(planner_verdict, "project_done", False))
    and not list(getattr(planner_verdict, "new_tasks", []) or [])  # planner has nothing to dispatch
)
```

When the Planner finishes the current stage's work and has nothing left to dispatch, it enters
`waiting`. This makes `planner_wait_reconciliation = True` and synthesizes a fake `review`
(status=`blocked`) whose instructions offer the Manager only **HOLD or ROLLBACK** — never ADVANCE.

### Site B — hard guard: `argus_skill/manager/_core.py:1883`

```python
if planner_wait_reconciliation and decision.action in {"advance", "complete"}:
    decision = StageDecision(
        "hold", cur,
        "planner waiting cannot advance without reviewer evidence",
        "planner_wait_advance_rejected",
    )
```

Even if the Manager LLM decides to `advance`, this guard **unconditionally** rewrites it to
`hold`. The stated intent is reasonable — *don't let the Planner advance a stage merely because
it ran out of busywork; advancement must be earned via reviewer evidence on the normal review
path (`review is not None`)*.

### Why it becomes a livelock

The scope-stage Reviewer certifications the Planner keeps producing are accepted as **sub-mission
completions** (`life.mission.completed` + `research.achievement.certified`), but they are **never
fed back into `decide_stage_transition` as a real `review` object**. Consequently:

1. Certification completes → Planner has no dispatchable tasks → `waiting = True`.
2. That is *exactly* the `planner_wait_reconciliation = True` condition (Site A).
3. Manager LLM would advance, but Site B force-holds with `planner_wait_advance_rejected`.
4. Supervisor persists a `manager-planner-feedback` telling the Planner "produce reviewer
   evidence" (`argus_skill/life/supervisor/_planning_cycle.py:361`).
5. Planner produces *another* scope certification → back to step 1.

The "reviewer evidence" the guard demands **already exists and is certified**, but the accepted
certification and the advance gate are on **two disconnected paths**, so the condition can never
be satisfied. The loop is structurally unescapable by the agents themselves.

## Reproduction

1. `argus-skill --continuous --objective "<kernel optimize task>"` on commit `61d95dc`, resolving
   to vertical `kernel_engineering`.
2. Let scope-stage artifacts get produced and certified once.
3. Observe: stage never advances past `scope`; `life.manager.stage_decision` repeats
   `hold` / `planner_wait_advance_rejected`; budget rises with no deliverable progress.

## Suggested fix directions (for maintainer)

1. **Connect certified achievements to the advance gate.** When
   `research.achievement.certified` fires for the current stage, inject it as a real `review`
   into `decide_stage_transition` so the *normal* advance path can fire — instead of the Manager
   only ever being reached via the `planner_wait_reconciliation` path.
2. **Make the Site B guard conditional on evidence existence.** Allow `advance` during
   `planner_wait_reconciliation` **iff** an accepted/certified reviewer achievement exists for
   `current_stage`. Today it blocks unconditionally even when the demanded evidence is present.
3. **Add livelock detection / circuit-breaker.** After N consecutive
   `planner_wait_advance_rejected` decisions that are each preceded by an accepted stage
   certification, escalate (operator alert) or force-advance, rather than re-issuing an
   (N+1)-th certification task and burning budget indefinitely.

## Raw log locations

- Session events: `~/.argus-skill/projects/s-b7783501/events.jsonl`
- Backlog snapshot: `~/.argus-skill/projects/s-b7783501/backlog.jsonl`
- Manager→Planner feedback: `~/.argus-skill/projects/s-b7783501/manager-planner-feedback-*.json`
- Scope artifacts (all present & certified): `~/wxy/argus-RMSNorm/research/`
  (`KERNEL_SCOPE.md`, `GROUND_TRUTH.md`, `PROJECT_NATIVE_SETUP.md`, `FIRST_SCORE_PLAN.md`, ...)
