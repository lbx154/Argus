---
name: Idea Discovery
description: Systematically mine recent literature for a real, falsifiable research gap before committing to an experiment plan. Supports method, systems, theory, diagnostic, characterization, evaluation, data, positive, negative, and boundary contributions.
category: paper-ideation
version: 1
created_at: 2026-06-01T00:00:00+00:00
---

# Idea Discovery — find a real gap, don't invent one

> Adapted from ARIS `idea-discovery` skill (MIT, © 2026 wanshuiyin).

Strong papers come from finding a **real gap or unresolved question** grounded
in the literature, not from imagining contributions top-down. A useful method,
system, theorem, benchmark, diagnosis, characterization, negative result, or
boundary finding may close that gap.

## When to invoke

- Project is in the `research` stage and has only a broad direction
  (e.g. "improve LLM reasoning")
- Engineer needs IDEA_CANDIDATES.md before plan stage
- Previous idea was killed by `kill-argument` and the project needs to pivot

## Workflow

### Step 1 — bound the direction

Engineer provides 1–2 sentence broad direction. Convert into 3–5
specific **trend-search queries** (NOT "improve LLM reasoning" but
"recent test-time compute results", "self-consistency vs sampling
diversity", etc.).

### Step 2 — multi-source literature scan

For each query, search **at least 3** of:
- arXiv (latest 12 months, filtered by ML/CL/AI)
- Semantic Scholar (citation-graph traversal from a recent strong paper)
- OpenAlex (cross-discipline coverage)
- ACL Anthology / OpenReview (venue-specific)
- HF Daily Papers (community-curated signal)

Pull abstracts + 1-paragraph TLDR for the top 30 hits per query.

### Step 3 — cluster + identify valuable open questions

Reviewer agent (gpt-5.5 via `author` route) reads the abstracts and
returns clusters of the form:

```
CLUSTER C-1: "Self-consistency helps math but hurts code generation"
  measured by: [Paper A, Paper B]
  current best / baseline: [the strongest reported method + its score]
  the gap: no method reliably keeps the math gain WITHOUT the code loss
  → research opening: <method, system, theory, diagnostic, evaluation, data,
    negative, or boundary contribution that resolves the question>
```

The reviewer ranks clusters by:
- **Stake** — "if resolved, X changes understanding or practice"
- **Research value** — would resolving this question change understanding,
  evaluation, system design, or practice?
- **Feasibility** — is there a credible staged execution plan within the
  operator's resources and time budget? Discover available capabilities and
  honor explicit limits; do not impose a universal wall-clock cutoff.
- **Recency** — are the closest references current enough to define the frontier?

### Step 3.5 — diagnose the bottleneck, then select a research move

Before writing candidates, sharpen each top-ranked cluster into a *structural
bottleneck* and choose the *research move* that closes it. This keeps generation
at "move-applied-to-gap" rather than free brainstorming.

1. **Method-lineage + gap type.** Arrange the 3–5 closest retrieved methods into
   a refine/replace lineage (each node refines or replaces an earlier one). From
   it, name ONE concrete structural gap and classify it:
   - **ADDITIVE** — an unmet need at a leaf; or
   - **SUBTRACTIVE** — a load-bearing assumption every method in the lineage
     inherits that you could *remove* (often the stronger, more surprising move).

   Then a **regression check**: confirm your fix is NOT something an older
   ancestor already did. The gap must rest on what the retrieved papers actually
   show, not on model recall.

2. **Select the research move (pattern).** Read the corpus-derived ideation
   patterns bundled with this skill:
   `references/ideation/ideation-patterns/overview.md` (15 patterns; each has a
   definition + operational signature + when-to-apply inlined — under
   `argus_builtin_skills/**/references/ideation/`; find by filename if the exact
   path differs). Pick the **1–3 patterns** whose operational signature
   structurally closes the gap (`ideation-patterns/companion-combos.md` shows
   which patterns pair into one paper — k=2 is the modal composition). The
   pattern is diagnostic vocabulary — never the contribution claim itself, and
   never a hard filter: a common pattern is fine if the delivery is substantive.

3. **Read the sub-pattern tactical card.** For the chosen pattern, open the ONE
   matching sub-pattern card in `references/ideation/ideation-sub-patterns/`
   (the `ideation-sub-patterns/overview.md` table maps every `C##` to its parent
   pattern). Follow its **Step-by-Step** to instantiate the mechanism, and read
   its **failure-mode** panel so the candidate visibly avoids that cluster's
   documented rejection (`references/ideation/anti-patterns.md` lists
   reject-enriched compositions to steer clear of).

### Step 4 — write IDEA_CANDIDATES.md

> **Pre-seeded candidates**: a codex live-web-search pass may have already
> appended candidates to `research/IDEA_CANDIDATES.md` under a
> `<!-- source: codex-web-search -->` marker (ids `WS-N`). Treat these as an
> ADDITIONAL source — MERGE and re-rank them alongside your own `I-N` clusters,
> do NOT overwrite them. Apply the operator's constraints (target venue,
> resource/time budget) here during ranking, not as a filter on the raw pool.

For each top-ranked cluster, produce:

```markdown
## Candidate I-1: <one line: the contribution and question it resolves>

**Problem & gap**: <what's open + the strong prior work/baseline that leaves it open>

**Bottleneck (gap type + regression check)**: <the structural gap from Step 3.5;
label ADDITIVE or SUBTRACTIVE; one line on the regression check — which ancestor
could already do this, and why yours differs>

**Research move (pattern → sub-pattern)**: <the selected pattern(s) by name and
the `C##` sub-pattern whose Step-by-Step you instantiated; name the failure mode
you are avoiding>

**Contribution shape**: <method, system, theorem, diagnostic, characterization,
evaluation, benchmark/data contribution, negative result, or boundary finding>

**Reference comparison + target**: <the strongest relevant published/standard
reference, the public benchmark(s), and what outcome would support or refute the
research claim>

**Why it matters (thesis)**: <one sentence — the non-obvious insight or
decision-relevant value>

**Experiment sketch (resource-adaptive)**:
- Setup: <models / data / baselines + the method>
- Falsifier: <what result would refute or materially weaken the claim>
- Compute & budget: <method-appropriate resources, staged execution, and the
  operator-approved budget>

**Local Feasibility** (read this turn's `## GPU Resource Allocation` /
`## Available APIs` / operator-constraint blocks and `nvidia-smi` — do NOT assume
a model/GPU you cannot actually run here; if the operator/direction states a
resource or time limit, that wins):
- Method runs on: <API-call | local inference | local training (LoRA/FT)>
- GPU memory needed vs free: <est. vs discovered free memory>
- **Can the probe answer the research question on public evidence within the
  available budget?**: YES / NO / CONDITIONAL
- **Executable on deployed setup**: YES / NO / CONDITIONAL (condition: <...>)

**Novelty bet**: <what makes this a new method, not a re-run of the cited work>

> A candidate with no credible evidence path inside the available resources is
> not executable yet. Stage it, narrow the claim, seek additional resources, or
> pivot before the signal-de-risk gate.

**Anticipated kill-argument**: <strongest 50-word rejection a hostile
reviewer would write; this skill must articulate it so kill-argument
later can stress-test it for real>
```

### Step 5 — hand off to idea-creator

`IDEA_CANDIDATES.md` is the input for `idea-creator`, which ranks
candidates against pilot budgets and selects 1–3 to pilot in parallel.

## Anti-patterns

- ❌ Start with "I want to do X" — the gap-discovery step is supposed
  to surprise you. If your candidate list is what you walked in with,
  you skipped the discovery.
- ❌ Dismiss a diagnostic, characterization, taxonomy, benchmark, or negative
  result merely because it does not propose a new model. Judge whether it
  resolves an important question with rigorous evidence.
- ❌ Reject a direction solely because it exceeds an arbitrary wall-clock
  threshold. Require a credible staged plan and explicit resource needs.
- ❌ Use only one literature source — confirmation bias by source
  bubble. Three independent sources minimum.

## Output contract

Writes `research/IDEA_CANDIDATES.md` ranked by novelty × research value ×
falsifiability × feasibility within the operator's actual budget. This is the
source of truth for the next skill
(`idea-creator`) and must be present before any experiment plan is written.
