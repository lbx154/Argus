---
name: Kill Argument
description: Two-thread adversarial review on a paper draft. Thread A — a fresh hostile reviewer with no prior context writes the strongest possible 200-word rejection memo (the "Reviewer 2 in a bad mood" simulation). Thread B — a second fresh reviewer reads the draft + memo and defends the paper point-by-point, surfacing still-unresolved critical issues. The remaining issues become must-fix-before-submission. Use after standard reviews pass; this is the final gate before submission.
category: paper-review
version: 1
created_at: 2026-06-01T00:00:00+00:00
---

# Kill Argument — adversarial attack/defense

> Adapted from ARIS `kill-argument` skill (MIT, © 2026 wanshuiyin).

Standard score-based reviews tend to produce **balanced** weakness
lists. Each weakness gets equal attention; reviewers never commit to
the single most damaging argument. Empirically this misses the one
sentence that, if a senior area chair reads it, kills the paper.

This skill forces commitment.

## When to invoke

- Draft has passed `review` stage (language / layout / infra clean)
- Before `submission` stage
- Also: when an idea passes `novelty-check` but you want to stress-
  test the conceptual case before committing pilot budget

Verdict-bearing — do not re-fire on a timer; the verdict only
changes when the *paper* changes.

## Workflow

### Thread A — attack (fresh hostile reviewer, no prior context)

Reviewer agent (gpt-5.5 via `reviewer` route, fresh thread, no
shared context) is given **only** the paper draft and this prompt:

```
You are a hostile area chair reading this paper as part of a
desk-reject decision. You have 200 words to write the strongest
possible rejection. Pick the SINGLE most damaging argument — not a
list of concerns, not balanced criticism. Commit to one knockout
sentence and build the 200 words around it.

The argument must be:
- Specific to THIS paper (not generic "needs more experiments")
- Cite a specific section / figure / number that supports the attack
- Survive a defender pointing out the obvious counter-argument
  (you should have already preempted that)

Hand in: <200-word rejection memo>.
```

Save the memo to `paper/KILL_ARGUMENT_ATTACK.md`.

### Thread B — defense (second fresh reviewer)

A different reviewer agent (fresh thread, no shared context with
Thread A) is given the paper + Thread A's memo:

```
You are a senior reviewer reading both this paper and a hostile
rejection memo from another reviewer. Your job:

1. Read the rejection memo carefully.
2. For each specific attack the memo makes, write a one-paragraph
   defense citing the paper's actual content (section / figure /
   number).
3. Identify which attacks you CANNOT successfully defend. List
   these as MUST-FIX issues with specific repair plans.
4. Render a verdict: SURVIVES (all attacks neutralized),
   PARTIALLY-SURVIVES (some attacks remain but paper can be
   defended in rebuttal), or KILLED (one or more attacks are
   fundamentally unanswerable; the paper needs structural rework).
```

Save the defense to `paper/KILL_ARGUMENT_DEFENSE.md`.

### Step 3 — extract must-fixes

From the defense's "MUST-FIX issues" list, create one work item
per issue in the project backlog with a specific repair plan.
The reviewer at `review` stage will gate on whether these are
addressed before re-running `kill-argument`.

If verdict was `KILLED`, the paper does NOT proceed to submission —
the planner is told to consider structural rework (different
framing, different headline result, possibly a different paper
entirely).

## Anti-patterns

- ❌ Allow Thread A and Thread B to share context — biases both
- ❌ Read the existing balanced reviews into Thread A — defeats
  the "commit to one knockout" point
- ❌ Soft-pedal the rejection memo — "be nice" produces useless
  attacks. The hostile prompt is the entire point
- ❌ Run more than once on the same draft — by the second run, the
  attacks become predictable. Run once, fix the must-fixes, run
  again only if the draft has materially changed

## Output contract

Writes `paper/KILL_ARGUMENT_ATTACK.md` and
`paper/KILL_ARGUMENT_DEFENSE.md`, plus appends MUST-FIX items to
the project backlog. The reviewer at `submission` stage reads the
defense's verdict; harness does not gate (the verdict is research-
quality, not structural).
