---
name: "Agent Team Lead"
description: "Shared Argus Team contract: recognize when parallel teammates are justified, preserve Team execution while planning, form and coordinate file-disjoint teammate work as the lead, and independently review the synthesis."
---

## Title
Agent Team Lead

## Description
Use a team only to parallelize several genuinely independent tasks. The lead writes a priority backlog and remains responsible for synthesis; the daemon-resident Curator claims tasks, starts one fresh teammate mission per claim, reaps it, and refills the pool. Solo execution is the default.

## Role authority

Every role may discover this Skill, but it does not erase role boundaries:

- Manager recognizes a Team request and preserves it in the mission handoff.
- Planner delegates Team formation unchanged; Planner does not infer availability
  from its own role-specific Skill directory.
- Engineer or an explicitly assigned lead forms and operates the Team.
- Reviewer remains read-only and audits teammate evidence plus the lead synthesis.
- Self may explain or route Team work, but does not execute it unless it is
  explicitly acting as the mission's Engineer/lead.

## Admission gate
Form a team only when all of these hold:

- At least two tasks can make useful progress concurrently.
- Their writable paths do not overlap.
- Each task has its own completion evidence.
- Provider, compute, and hardware capacity can support the requested width.

Stay solo for small, sequential, tightly coupled, or same-file work. `owns_paths` records the lead's partition for review and prior-work inheritance; it is not a filesystem sandbox, so do not form a team when prompt-level ownership is insufficient.

## Form the rolling backlog
Use `python -m argus_skill.tools.team`.

1. Write one JSON object per line in `tasks.jsonl`:
   `{task_id, title, objective, acceptance_check, owns_paths, deps?, priority?, timeout_s?, target?, lower_is_better?, cwd?}`.
   Lower `priority` runs first. Prefix task IDs with the team ID. A task-specific `cwd` wins; otherwise the campaign `--cwd` is used. Set `cwd` only for a task that is its own project tree — a task working inside the campaign tree keeps the campaign `cwd` and takes its private directory through `owns_paths`, or it is cut off from the project state the campaign shares.
   Use `timeout_s` for genuinely bounded work; the Curator and teammate runner
   both enforce it, while omitted/zero retains the campaign default.
2. Run:
   `form --root <team_root> --team-id <tid> --cwd <workspace> --mission "<objective>" --tasks tasks.jsonl`.
3. Set deliberate capacity with:
   `pool-set --root <team_root> --width <N> --state running`.
4. Inspect progress with `status --root <team_root>` and read landed `shards/*.jsonl` plus `leaderboard.json`.
5. A task waiting on a real operator-owned decision is `blocked`, retains its owner and question, and is not retried. After the operator answers, run `resume --root <team_root> --task-id <task_id> --answer "<answer>"` to requeue it with that answer.
6. Refresh or extend the backlog with `form`. Re-forming claimed, running, or blocked work preserves its lifecycle state; re-forming a done or failed task deliberately reopens it.
7. Wind down with `pool-set --state draining`, synthesize the canonical artifact, pass the normal mission Reviewer, then run `dissolve --root <team_root>`.

The lead never manually spawns, claims, waits for, reassigns, or kills teammates. Those are Curator responsibilities.

## Task-objective contract
Every task must state:

- the bounded objective, and the separately checkable done condition as the task's `acceptance_check`, naming exactly once the single subject the task must move (the claim, kernel, or artifact id): a vertical's per-mission context block is resolved from the first task field that names exactly one, and a field naming two resolves to none;
- the only paths it may modify;
- the required artifact/result-shard handoff;
- the real measurement or verification command;
- anti-fraud and resource constraints relevant to the task.

A teammate runs one normal Engineer→Reviewer mission and exits. The Curator then refills the freed slot; a teammate does not claim a second task itself. Fresh teammates receive the deterministic leaderboard block when available.

## Result and synthesis rules

- Teammates emit task-local artifacts and one shard; they never write the shared leaderboard or the lead's canonical merged artifact.
- The Curator is the single writer for pool lifecycle and deterministic leaderboard folding.
- The lead accepts measured, task-valid results only and is the single writer of the canonical synthesis.
- Every teammate result passes its own Reviewer; the final synthesis still passes the mission Reviewer.

## Anti-patterns

- Forming a team without real parallel work.
- Overlapping writable paths or sharing one mutable output file.
- Treating `owns_paths` as mechanically enforced isolation.
- Manually launching teammate processes beside the Curator.
- Ranking an unverified number or treating a failed shard as a valid best result.
- Letting coordination bookkeeping replace the requested engineering work.
