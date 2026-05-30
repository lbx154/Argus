---
name: ablation-planner
description: "Design ablation studies that answer reviewer questions. Identifies which components to remove/replace, prioritizes by impact and compute cost, and produces runnable experiment configs. Use after main results pass result-to-claim."
category: experiment-design
version: "1.0"
scientist_model: gpt-5.5
created_at: "2025-07-27"
---

# Ablation Planner

Systematically design ablation studies that answer the questions reviewers will ask.

## When to Use

- Main results pass result-to-claim with claim_supported = yes or partial
- User explicitly requests ablation planning
- Paper review identifies missing ablations

## Workflow

### Step 1: Prepare Context

Read project files to build the full picture:
- Method description and components (from research contract or AGENTS.md)
- Current experiment results (from EXPERIMENT_LOG.md or result files)
- Confirmed and intended claims
- Available compute resources

### Step 2: Design Ablations

Think like a rigorous ML reviewer. For the given method and results, design ablations that:

1. **Isolate contribution** of each novel component
2. **Answer reviewer questions** they will definitely ask
3. **Test sensitivity** to key hyperparameters
4. **Compare alternatives** — natural design choices you didn't pick

For each ablation specify:
- **name**: what to change (e.g., "remove module X", "replace Y with Z")
- **what_it_tests**: the specific question this answers
- **expected_if_component_matters**: prediction if the component is important
- **priority**: 1 (must-run) to 5 (nice-to-have)
- **type**: config-only | code-change
- **estimated_time**: relative cost

### Step 3: Produce Ablation Plan

```markdown
## Ablation Plan

### Component Ablations (highest priority)
| # | Name | What It Tests | Expected If Matters | Priority | Type |
|---|------|---------------|---------------------|----------|------|
| 1 | remove module X | contribution of X | drops on metric Y | 1 | config |
| 2 | replace X with simpler Z | value of learned vs fixed | drops on dataset A | 2 | code |

### Hyperparameter Sensitivity
| # | Parameter | Values to Test | What It Tests | Priority |
|---|-----------|---------------|---------------|----------|
| 3 | lambda | [0.01, 0.1, 1.0] | sensitivity to regularization | 3 |

### Design Choice Comparisons
| # | Name | What It Tests | Priority |
|---|------|---------------|----------|
| 4 | joint vs separate | whether joint training adds value | 4 |

### Coverage Assessment
[What reviewer questions these ablations collectively answer]

### Unnecessary Ablations (skip these)
[Experiments that seem useful but won't add insight]

### Run Order
[Optimized for maximum early information — run highest-info first]

### Estimated Total Compute
[GPU-hours or wall-clock estimate]
```

### Step 4: Feasibility Review

Before running, check:
- **Budget**: can we afford all ablations?
- **Code changes**: which need code mods vs config-only?
- **Dependencies**: which can run in parallel?
- **Cuts**: if budget tight, propose removing lower-priority and justify

### Step 5: Implement

1. Create configs/scripts for each ablation (config-only first)
2. Smoke test each before full run
3. Run in suggested order with descriptive names
4. Track in EXPERIMENT_LOG.md
5. After all complete → feed results to result-to-claim

## Rules

- Every ablation must have a clear `what_it_tests` — no "just try it" experiments
- Config-only ablations > code-change ablations (faster, less error-prone)
- Component ablations (remove/replace) > hyperparameter sweeps
- Do not generate ablations for components identical to baseline (no-op)
- Record ALL results including negative (no effect = important finding)
- If total compute exceeds budget, propose cuts — don't silently drop

## Integration

- Triggered by `result-to-claim` when verdict = yes + ablations needed
- Results feed back into `result-to-claim` for final claim confirmation
- Consumed by `emnlp-paper-drafting` for the ablation table/section
