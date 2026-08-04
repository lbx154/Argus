---
name: "Agent Team Lead"
description: "How the engineer acts as a team lead — decompose a mission into file-disjoint subtasks, spawn self-looping teammate engineers, coordinate via a shared task board and mailbox, then synthesize and gate the merged result."
---

## Title
Agent Team Lead

## Description
Use a team only to parallelize several genuinely independent tasks. The lead writes a priority backlog and remains responsible for synthesis; the daemon-resident Curator claims tasks, starts one fresh teammate mission per claim, reaps it, and refills the pool. Solo execution is the default.

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
   `{task_id, title, objective, owns_paths, deps?, priority?, target?, lower_is_better?, cwd?}`.
   Lower `priority` runs first. Prefix task IDs with the team ID. A task-specific `cwd` wins; otherwise the campaign `--cwd` is used.
2. Run:
   `form --root <team_root> --team-id <tid> --cwd <workspace> --mission "<objective>" --tasks tasks.jsonl`.
3. Set deliberate capacity with:
   `pool-set --root <team_root> --width <N> --state running`.
4. Inspect progress with `status --root <team_root>` and read landed `shards/*.jsonl` plus `leaderboard.json`.
5. Refresh or extend the backlog with `form`. Re-forming a live task preserves its owner; re-forming a terminal task deliberately reopens it.
6. Wind down with `pool-set --state draining`, synthesize the canonical artifact, pass the normal mission Reviewer, then run `dissolve --root <team_root>`.

The lead never manually spawns, claims, waits for, reassigns, or kills teammates. Those are Curator responsibilities.

## Task-objective contract
Every task objective must state:

- the bounded objective and separately checkable done condition;
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
