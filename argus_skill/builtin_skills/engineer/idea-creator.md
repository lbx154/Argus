---
name: "Idea Creator"
description: "Given IDEA_CANDIDATES.md from idea-discovery, rank candidates and run the cheapest faithful falsification or characterization probe within the operator's budget. Positive, negative, diagnostic, and boundary findings may all justify an experiment plan when they have research value."
---

# Idea Creator — rank, pilot, commit

> Adapted from ARIS `idea-creator` skill (MIT, © 2026 wanshuiyin).

`idea-discovery` produces candidates; `idea-creator` decides which deserve real
budget. The probe budget is set by the operator and project, not a universal
wall-clock threshold. The reviewer rules on whether the resulting positive,
negative, diagnostic, or boundary evidence is scientifically valuable.

## When to invoke

- `research/IDEA_CANDIDATES.md` exists
- Project hasn't yet committed to an experiment plan
- Budget allows a faithful bounded probe (operator-set, not harness-set)

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

### Step 2 — design probes for the top candidates

For each `run`-recommended candidate, write a **resource-adaptive probe spec**.
The probe should cheaply test the binding premise or characterize the proposed
phenomenon against a strong reference. Do not force a fixed duration or require
an improvement when a clean null/boundary result would answer the question.

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
- Signal clearly absent → record a supported negative or kill the hypothesis,
  depending on whether the result has research value
- Signal ambiguous → enlarge once if justified, then classify honestly
```

### Step 3 — execute pilots in parallel

Run probes via `research-experiment-runner`. Parallelism is optional; use it only
when probes are independent and resources allow it.

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

- ❌ Pilot all candidates fully instead of using the cheapest faithful probe
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
