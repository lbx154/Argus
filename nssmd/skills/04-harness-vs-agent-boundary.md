---
name: harness-vs-agent-boundary
description: The harness must never make domain (research-quality) judgments. Hard-coded thresholds, magic numbers, and "good enough" verdicts belong to the agent, not to Python code in the harness.
when_to_invoke: Before any change that adds a `check_*` / `validate_*` / `gate_*` function with a numeric threshold, comparator, or "pass/fail on quality" verdict.
---

# Harness vs Agent — the only boundary that matters

## The rule

The harness is **领域无关的笨管道**. It owns:

- **Plumbing** (predicate verbs: load, save, schedule, render, parse, persist)
- **Budgets** (token caps, daily caps, per-mission caps)
- **Anti-fraud guards** (claim → evidence chain integrity, bundle provenance, tainted-marker detection, replay sanity)
- **Structured I/O** (the contract by which agents talk to harness — schemas, exit codes, journals)

The agent owns:

- **All taste / quality / "good enough" verdicts** (is this baseline reproduced strongly enough? is 2% improvement publishable? are 3 benchmarks broad enough?)
- **All domain-specific judgments** (is this hypothesis novel? does this evidence actually support this claim? would EMNLP reviewers accept this?)
- **Final decision authority** for the round (reviewer ruling is the only source of truth for "done")

## The test

Before adding any code to argus_skill/, ask:

> *Does this code make a judgment that a thoughtful research-aware reviewer would also need to make?*

- **YES** → It does not belong in the harness. Move the judgment to a prompt / checklist the reviewer reads; have the harness only surface facts.
- **NO** → It's plumbing. Probably fine.

## Concrete examples

### ✅ Harness (correct)

| Code | Why it's fine |
|---|---|
| `evidence_chain.py` checks every cited file exists | Anti-fraud: "you can't cite a path that doesn't exist." Not a quality call. |
| `LifeBudget.can_start()` blocks at daily cap | Plumbing: spent_usd >= daily_cap is arithmetic, not judgment. |
| `BUILD_INFO.md` must exist in every bundle | Provenance / replayability. Not "is this evidence good." |
| Tainted-marker (`TAINTED — DO NOT CITE`) is a hard block on non-historical claims | The bundle was self-labeled tainted. Honoring that label = anti-fraud, not judgment. |
| Quarantine on `spent_usd >= 0.80 * budget_usd && !has_draft` | Budget plumbing. The threshold is a *spending* threshold (numeric and operator-set), not a *research-quality* threshold. |

### ❌ Harness (forbidden — these are agent calls)

| Code | Why it's wrong |
|---|---|
| `min_delta = 0.02` to "decide if improvement is meaningful" | 0.02 reward delta is publishable on benchmark A and noise on benchmark B. The reviewer decides, not Python. |
| `min_benchmark_families = 3` to "decide if evidence is broad enough" | Some areas need 2 strong benchmarks; some need 5. Domain call. |
| `baseline_zero_reward` flag means "baseline failed → block the round" | "Is reward > 0 enough proof the baseline reproduced?" is a judgment. Surface the number; let the reviewer rule. |
| `auto-quarantine after 21 days in writing stage` | "21 days writing is too long" is a research-tempo judgment. Emit an *advisory* signal; let planner decide. |
| `keyword heuristics` for whether an objective is in-scope | Already deleted upstream (`6a90f55`); never resurrect. |

## How to fix when you're tempted to add a judgment

1. **Identify the fact** the agent would use to make the call.
   - e.g. "What's the baseline reward?" "How many benchmark families are present?" "How long has this been in writing?"
2. **Compute and surface the fact** as a structured finding, never a pass/fail verdict.
   - The harness prints: `"baseline best aggregate reward = 0.62, proposed = 0.66, delta = +0.04, families covered = [tb2@2.0, swebpro@1.0]"`
   - The harness does NOT print: `"FAIL: improvement below 0.02 threshold"`.
3. **Route the finding to the reviewer's prompt** via the existing CheckResult.output_tail / stage_check stdout path.
4. **Never let the finding affect the exit code** of stage_check, unless it's a structural / anti-fraud check (see the table above).
5. **Let the reviewer rule.** ReviewDecision.status is the only source of truth.

## Decision flowchart

```
Adding a new gate / check / validator?
        │
        ▼
"Could a research-aware reviewer disagree with this check's verdict,
 depending on the project's domain and goals?"
        │
   ┌────┴────┐
   │ YES     │ NO
   ▼         ▼
ADVISORY    STRUCTURAL
finding      gate
(no exit     (can exit
 code        non-zero,
 effect,     hard-block
 just        round)
 render
 facts)
```

## When in doubt

Default to advisory. It's always safer to surface a number the agent reads
than to bake a threshold into Python. The cost of an over-permissive
advisory is one wasted reviewer turn; the cost of a wrong hard-coded
threshold is the entire system silently rejecting research the agent
would have accepted.

## Reference incident

`c6b11d3` (the original F3 anti-mediocrity gate) violated this rule with
`DEFAULT_MIN_DELTA = 0.02` and `DEFAULT_MIN_FAMILIES = 3` baked into Python
and counted into stage_check exit code. It was rejected in review
`review/2026-06-01-research-factory-gates-c6b11d3.md`. The post-rewrite
version (`<commit>`) surfaces the same facts as a structured advisory
finding without any threshold logic and never affects exit code; F4
evidence_chain (structural / anti-fraud) was kept as a hard block, which
is correct.
