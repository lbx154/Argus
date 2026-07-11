---
name: Idea Creator
description: Given IDEA_CANDIDATES.md from idea-discovery, rank candidates and pilot the top 1-3 in parallel within a budget. Each pilot is a minimal cheap proof-of-concept (resource-adaptive, ≤3h/run) that shows whether the proposed method beats a reproduced baseline, producing a kill-or-keep verdict. Pilots that pass become the experiment plan; pilots that fail are documented and pivot to the next candidate.
category: paper-ideation
version: 1
created_at: 2026-06-01T00:00:00+00:00
---

# Idea Creator — rank, pilot, commit

> Adapted from ARIS `idea-creator` skill (MIT, © 2026 wanshuiyin).

`idea-discovery` produces candidates; `idea-creator` decides which to
spend real budget on. A candidate that survives a 2-hour pilot earns
the experiment plan slot; one that dies in pilot pivots to the next
candidate. The reviewer agent rules on "is this still worth pursuing",
not the harness.

## When to invoke

- `research/IDEA_CANDIDATES.md` exists
- Project hasn't yet committed to an experiment plan
- Budget allows 2-hour pilots (operator-set, not harness-set)

## Workflow

### Step 1 — rank candidates

Reviewer agent (gpt-5.5 via `author` route) reads
`IDEA_CANDIDATES.md` and ranks by joint **novelty × tractability × stake ×
local_feasibility** — read each candidate's `Local Feasibility` block:

```json
{
  "ranking": [
    {"id": "I-1", "novelty": "high", "tractability": "med",
     "stake": "high", "local_feasibility": "executable", "rank_score": 0.81,
     "pilot_recommendation": "run"},
    {"id": "I-2", "novelty": "med", "tractability": "high",
     "stake": "med", "local_feasibility": "conditional", "rank_score": 0.62,
     "pilot_recommendation": "queue"},
    {"id": "I-3", "novelty": "high", "tractability": "high",
     "stake": "high", "local_feasibility": "unfeasible", "rank_score": 0.0,
     "pilot_recommendation": "drop"}
  ]
}
```

`local_feasibility` ∈ {`executable`, `conditional`, `unfeasible`, `unknown`}
comes straight from the candidate's `Local Feasibility` block (does the core
signal MOVE on a model this box can actually run?). **An `unfeasible` candidate
must NOT be recommended `run`** no matter how novel — a signal that cannot move
locally is a dead pilot (e.g. a safety idea on a frontier API that refuses every
harmful prompt). The reviewer rules on scores; the harness does not impose a
threshold, but piloting an `unfeasible` idea is forbidden — it would only be
killed at the signal-de-risk gate after wasting the pilot.

### Step 2 — design pilots for the top 1-3

For each `run`-recommended candidate, write a **resource-adaptive pilot spec**
(a cheap proof the method beats a reproduced baseline; ≤3h/run, and the eventual
main experiment must fit ≤8h wall-clock):

```markdown
## Pilot P-{{id}}: <one-line goal>

**Falsifiable hypothesis**: <claim from IDEA_CANDIDATES.md>

**Minimum signal**: <smallest measurement that would already
distinguish hypothesis from null>

**Setup**:
- Models: <subset>
- Prompts: <N samples, source>
- Trial count: <minimum-N for the signal to be visible>
- Token budget: <estimate>

**Stop rules**:
- Signal clearly present → commit to full experiment plan
- Signal clearly absent → mark candidate as `pilot_killed`, pivot
- Signal ambiguous → enlarge to next-N pilot (one more pass), then commit/kill
```

### Step 3 — execute pilots in parallel

Run pilots via the existing `agent-research-benchmark-runner` skill;
do NOT block on each other. Pilots run resource-adaptively within the
per-run budget (cheap inference, or a short ≤3h LoRA/FT run on the available
GPUs), which keeps the cost bounded to the operator's budget.

### Step 4 — record verdicts

Each pilot writes:
- `experiments/pilot-{{id}}/RESULTS.md` — measurement summary
- `experiments/pilot-{{id}}/VERDICT.md` — reviewer-written
  commit/kill verdict with evidence

### Step 5 — commit to one candidate

The reviewer reads all pilot verdicts and selects ONE candidate to
build the full experiment plan around. That selection goes into
`research/EXPERIMENT_PLAN.md` (input to the `plan` stage).

## Anti-patterns

- ❌ Pilot all candidates fully instead of cheaply — wastes budget
  on candidates that would have died in 2 hours
- ❌ Mark "ambiguous" as commit — ambiguous pilots usually become
  ambiguous full experiments
- ❌ Skip the pivot step when pilot kills — commit-bias is the
  number-one cause of dead-end papers
- ❌ Re-pilot a killed candidate to "make sure" — the kill verdict
  was made on evidence; treat it as final unless the candidate is
  re-specified

## Output contract

Writes `research/IDEA_RANKING.json`,
`experiments/pilot-*/{RESULTS,VERDICT}.md`, and updates
`research/IDEA_CANDIDATES.md` with `pilot_status` per candidate. The
final commit is recorded in `research/EXPERIMENT_PLAN.md`.
