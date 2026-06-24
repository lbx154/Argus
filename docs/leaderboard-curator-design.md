# Leaderboard Curator — Design

**Status:** implemented (2026-06-24). Operator-layer (`code/leaderboard_curator.py`
+ `/tmp/argus_curator.sh` + `form_concentrated.py` injection). General by design;
SOL-ExecBench is the first consumer.

## Problem

A rolling pool of optimizer-teammates grinding a measured benchmark wastes ~95%
of its scoring compute. Measured over 14h on SOL: **487 candidates scored, only
~5% beat their kernel's current best**; the rest re-derive mechanisms that were
already tried and plateaued (e.g. L1_077 had **38 distinct mechanisms** tried —
4 `copyback` variants at ~0.10, a half-dozen cuBLASLt variants all stuck at
0.068). Each redundant candidate still consumes a full B200 score (~6–40 min).

Root cause: the search is **uncoordinated at the mechanism level**. Teammates
have per-kernel memory (a 12k-line prose `idea_wiki`) but no *structured*,
queryable "(mechanism → score)" ledger the search can use to (a) avoid
re-trying, and (b) pick the highest-EV *unexplored* direction.

A naive fix — "tell the agent to maintain a leaderboard" — recreates the
`GROUND_TRUTH` plumbing virus: agent-maintained shared structured state becomes a
ritual that crowds out the actual work (see the 2026-06-23 root-cause: ~14k
self-generated `diagnostics_*`/`GROUND_TRUTH`/`*required_poll*` files, none
human-mandated).

## Design

A **dedicated curator** OWNS a structured per-target leaderboard built from the
scoring evidence. **Teammates only EMIT results (their own evidence/shard) and
READ an injected leaderboard block — they never write the shared ledger.**

The safety invariant is a **frequency split** — who writes what, how often:

| Layer | Who | Frequency | What |
|---|---|---|---|
| **1. fold** | harness (deterministic) | high (180s) | scan `benchmarks/evidence/*/*/manifest.json` → `research/leaderboard.json` (per-target: best + `attempts[{mechanism, sol, status}]`). Single-writer, atomic. **Never agent-maintained.** |
| **2. distill** | agent (codex) | low (~21min) | read the leaderboard (numbers) + `idea_wiki` (the "why") → for the worst N targets, the single **highest-EV UNEXPLORED direction** → `research/strategy.md`. This is the leaderboard ↔ memory-wiki linkage. Best-effort. |
| **3. objective_block** | injected into teammate objective | per task-form | best-so-far + the tried-mechanism table (**DO NOT re-derive**) + the curator's next-direction strategy. Read-only for the teammate. |

Data flow:

```
teammates --emit--> benchmarks/evidence/<kernel>/<cand>/manifest.json
                                   |
                    (1) fold  [deterministic, single-writer]
                                   v
                        research/leaderboard.json  <----+
                                   |                     |
                    (2) distill [agent: codex]           |
                                   v                     |
                         research/strategy.md            |
                                   |                     |
                    (3) objective_block(kernel) ---------+
                                   |
              form_concentrated injects into task.objective
                                   v
                    fresh teammate READS it (never writes)
```

## General by construction (not SOL-specific)

The schema — `best + attempts(mechanism, score, status)` — is **task-agnostic**.
The only task-specific knowledge is the **adapter**: how to read a candidate's
score + mechanism tag from its evidence. That lives in
`research/leaderboard_schema.json` (DATA, not code):

```json
{ "score_fields": ["sol_score", ...], "status_field": "all_passed",
  "mechanism_strip_leading": "^.*?h\\d+_", "mechanism_strip_trailing": [...] }
```

`init` writes the default (SOL). For a brand-new task an agent generates this by
inspecting the task's scorer/harness (layer-1 auto-init) — **no curator code
changes per task.** A new measured benchmark reuses the whole curator by shipping
its own `leaderboard_schema.json` + an `objective_block` injection point.

## Why this is safe (does not become the next plumbing virus)

1. **Teammates never write the shared ledger** — they only emit their own shard
   (no shared-state contention) and read a read-only injected block.
2. **High-frequency maintenance is deterministic code**, not an agent ritual —
   no "I must update the leaderboard" reasoning in the optimizer loop.
3. **The agent only does the low-frequency, high-value reasoning** (next
   direction), which is value, not bookkeeping; failure degrades to the prior
   strategy and the deterministic table still ships.

## Components

- `code/leaderboard_curator.py` — `init` / `fold` / `distill` / `block`.
- `/tmp/argus_curator.sh` — the dedicated living curator loop (tmux `curator`);
  supervised (relaunched by `argus_supervisor.sh` if it dies).
- `form_concentrated.py` — folds, then prepends `objective_block(kernel)` to each
  formed task objective.
- Artifacts: `research/leaderboard.json`, `research/strategy.md`,
  `research/leaderboard_schema.json`.

## Next layer (not yet built)

- Cross-family rollup (the MoE/attention families share a mechanism account so 4
  MoE teammates don't independently re-discover the same idea).
- Cheap-proxy scoring (single representative shape) as a pre-filter before the
  full aggregate score — multiplies effective throughput, complements the ledger.
