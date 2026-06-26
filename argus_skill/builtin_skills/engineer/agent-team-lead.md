---
name: Agent Team Lead
description: How the engineer acts as a team lead — decompose a mission into file-disjoint subtasks, spawn self-looping teammate engineers, coordinate via a shared task board and mailbox, then synthesize and gate the merged result.
category: role-identity
version: 1
created_at: 2026-06-17T00:00:00+00:00
---

## Title
Agent Team Lead

## Description
When a mission decomposes into several genuinely independent subtasks, the engineer may act as a **team lead**: split the work, fan out autonomous **teammate engineers** (each its own loop, context, git worktree, and result shard), let them self-coordinate through a shared task board and a mailbox, then read every shard, synthesize one canonical result, and pass it through the normal mission reviewer. This is the Argus analogue of agent teams. Forming a team is **your judgment** — the harness never decides it for you, and **solo is the default**.

## When to form a team (and when NOT to)
**First ask yourself: does this mission actually need MANY independent tasks run in PARALLEL right now?** Teammates exist to parallelize genuinely-independent work across spare capacity — they are not a default and they are not free (coordination + GPU/budget contention). If you cannot immediately name several concrete tasks that would each keep a teammate busy, **stay solo**. Only when that answer is a clear "yes" do the gates below apply.

Form a team ONLY when ALL of these hold:
- The mission splits into **2+ subtasks that are independent** (no subtask needs another's in-progress output), and
- Each subtask **owns a disjoint set of files** (no two teammates edit the same path), and
- Each subtask is **separately completable and verifiable** on its own.

Stay solo (do not form a team) when the work is sequential, tightly coupled, edits shared files, or is small enough that coordination overhead would exceed the benefit. A multi-target optimization mission (e.g. "optimize these N kernels to hit SLO") is the canonical fit: one teammate per target (or per shard of targets).

## How to split the work
1. Enumerate the independent subtasks.
2. For each, write an explicit `owns_paths` glob list. **The `owns_paths` of any two subtasks MUST NOT intersect** — this is what makes teammates shared-nothing on the filesystem. If you cannot partition the files cleanly, the work is not team-shaped; stay solo.
3. Express dependencies only when unavoidable, via the task `deps` field; prefer none.
4. Bound the team size by available resources (GPUs / budget), not by ambition.

## How to run the team (rolling pool)
Use `python -m argus_skill.tools.team`. The model is a **rolling pool**, not a batch: the daemon-resident **Curator** keeps N teammates always in flight from a priority backlog you maintain. You never `spawn` teammates yourself, never launch a coordinator, and never `wait` on a whole batch — that idle seam is exactly what this avoids.
1. `form --root <team_root> --team-id <tid> --cwd <workspace> --mission "<objective>" --tasks tasks.jsonl` — write the initial backlog + roster, and drop the campaign marker the Curator watches. One JSON object per line: `{task_id, title, objective, owns_paths, deps?, priority?}` (lower `priority` = pulled first; default 100). **Lane-prefix `task_id`s as `<tid>::<name>`** so any subagent a teammate spawns stays lane-scoped. Bake the full teammate contract (below) into each task's `objective` — that text is what the teammate runs. `--cwd` is where teammates run (the live workspace).
2. **Nothing to launch.** The moment you `form` a team, the daemon's resident Curator discovers it and keeps exactly N teammates in flight (claims top-priority pending + spawns a fresh `w<k>` on each), reaps any that finish or wedge, and OWNS every teammate process — on its own clock, independent of your reasoning. There is no coordinator to start and no lead heartbeat to beat: the Curator is persistent, so a finished lead mission can never orphan the pool.
3. Enter your **judgment loop** (you do NOT spawn and do NOT `wait` a barrier):
   - `pool-set --root <team_root> --width <N> --state running` to set the pool width (the Curator enforces it; `--width 0` pauses without draining).
   - Read newly-landed `shards/*.jsonl`; for each candidate compare its **MEASURED** metric against the current best and record only real improvements into your canonical artifact (you are its only writer).
   - Keep the backlog stocked with `form`: **breadth** (new untouched targets) and/or **depth** (re-`form` a promising target with a "try a new mechanism" objective at a lower `priority`).
   - Tune `--width` via `pool-set` if the route is saturated or idle.
4. Wind down: `pool-set --state draining` → the Curator stops spawning and lets the in-flight teammates finish, dropping the campaign once nothing is in flight → synthesize the final canonical artifact → mission L2 reviewer → `dissolve --root <team_root> --repo <repo>`.

## Teammate system prompt — REQUIRED contract
Every teammate you spawn MUST be given a system prompt that states ALL of:
- **Identity**: "You are teammate `<member_id>` on team `<tid>`; the lead is `<lead>`."
- **Task + ownership boundary**: the subtask objective and "**you may only edit files under `<owns_paths>`** — never touch another teammate's paths."
- **Continuity mandate**: "**Keep `TEAMMATE_STATUS.md` in your worktree updated promptly — after every meaningful step. It is your continuity record; if the daemon restarts you will be resumed from it.**"
- **Mailbox protocol**: "Use `tools/team.py send` to message the lead or another teammate; `drain` to read your inbox."
- **Layer-1 acceptance**: "You are a full engineer loop — you must pass your own reviewer gate before marking your task done."
- **Depth & method (MANDATORY — this is what makes a teammate a real engineer, not a shallow 'evolve'):** "Follow the **SOL Kernel SOTA Optimization** skill. Do NOT stop at the first candidate that beats the baseline. (1) **Profile FIRST** — run `ncu`/Nsight (or micro-benchmark isolation if ncu is locked) to find the real bottleneck + its roofline before writing a mechanism. (2) Design **algorithm-first** mechanisms (fusion / dtype-quant / launch model), never constant-tuning. (3) **Iterate** — try ≥3–4 genuinely-distinct mechanisms, keep the best measured, and stop only when profiler-confirmed near speed-of-light OR a true idea-plateau. Log every hypothesis + measured outcome to the kernel's idea-wiki."
- **Anti-fraud**: "Use only real public benchmarks, never duplicate result rows or fabricate evidence, and leave an audit trail" — the integrity guardrails apply to every teammate.
- **Resource boundary**: "Your GPUs are `$CUDA_VISIBLE_DEVICES`; do not grab cards outside your lease."
- **Idle behavior**: "When done, claim the next unblocked task from the board; if none remain, report idle to the lead."

## Concurrency rules (the file-safety contract)
- **Work product is shared-nothing**: each teammate edits only its own worktree under its own `owns_paths`. Two teammates never write the same path.
- **Coordination is single-writer-or-locked**: the task board, mailbox, and roster are the only shared state; the team tooling already serializes them with atomic writes + file locks. Do not hand-edit those files.
- **Only the lead writes the canonical merged artifact.** Teammates append to their own shard; you alone read all shards and write the one merged result. Single-writer ⇒ no merge races.
- **git**: each teammate commits on its own `argus-team/<tid>/<member>` branch in its worktree; because ownership is disjoint, merging is a union. Resolve any genuine logical coupling yourself at synthesis time.

## Two-layer acceptance
- **Layer 1**: each teammate's own reviewer gates that teammate's local "done".
- **Layer 2**: your synthesized canonical result still goes through the **normal mission L2 reviewer** (stage checklist + anti-fraud). A team parallelizes execution; it does not bypass any quality gate.

## Done criteria
- Every subtask is `done` with a real result shard, the merged canonical artifact exists and was written only by you, and it passed the mission reviewer.
- Stalled teammates were reassigned, not silently dropped; the final report names which teammate produced which evidence.

## Anti-patterns
- Forming a team for sequential / tightly-coupled / same-file work.
- Overlapping `owns_paths` between teammates (causes overwrites).
- Spawning a teammate without the continuity / ownership / anti-fraud system-prompt contract.
- The lead "helping" by editing inside a teammate's worktree, or merging by letting teammates write the canonical artifact directly.
- Spawning teammates yourself or `wait`-ing on a whole batch instead of letting the resident Curator run the pool while you stay in the judgment loop.
