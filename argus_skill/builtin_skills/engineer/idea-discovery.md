---
name: Idea Discovery
description: Systematically mine recent literature for a real research gap before committing to an experiment plan. Searches arXiv / Semantic Scholar / OpenAlex / ACL Anthology, clusters trends, identifies measured-and-confirmed phenomena that nobody has yet given a mechanism for, and writes IDEA_CANDIDATES.md ranked by novelty × tractability. Train-free; the gap-discovery itself runs purely on inference + literature lookup.
category: paper-ideation
version: 1
created_at: 2026-06-01T00:00:00+00:00
---

# Idea Discovery — find a real gap, don't invent one

> Adapted from ARIS `idea-discovery` skill (MIT, © 2026 wanshuiyin).

Most "novel ideas" come from finding **already-measured phenomena that
nobody has explained**, not from imagining mechanisms top-down. This
skill systematically mines recent literature for those gaps.

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

### Step 3 — cluster + identify "measured-but-unexplained"

Reviewer agent (gpt-5.5 via `author` route) reads the abstracts and
returns clusters of the form:

```
CLUSTER C-1: "Self-consistency helps math but hurts code generation"
  measured by: [Paper A, Paper B]
  explained by: [partial speculation in Paper A's discussion]
  unexplained: WHY does it hurt code? Mechanism unknown.
  → research gap: explain the mechanism with controlled study
```

The reviewer ranks clusters by:
- **Stake** — "if true, X changes practice; if false, refutes Y"
- **Tractability** — measurable train-free (only API calls)?
- **Recency** — is the phenomenon fresh enough to publish a mechanism for?

### Step 4 — write IDEA_CANDIDATES.md

For each top-ranked cluster, produce:

```markdown
## Candidate I-1: <one-line mechanism hypothesis>

**Phenomenon to explain**: <what's measured + by whom>

**Hypothesis**: <falsifiable claim about mechanism>

**Train-free experiment sketch**:
- Setup: <prompts / models / measurements>
- Falsifier: <what observation would refute the hypothesis>
- Approximate budget: <token count, wall time>

**Local Feasibility** (read this turn's `## GPU Resource Allocation` /
`## Available APIs` / operator-constraint blocks — do NOT assume a model/GPU you
cannot actually run here):
- Critical signal comes from: <API-call output | gradient/training | inference>
- Needs training? <yes/no>; GPU memory needed vs available: <est. vs this box>
- **Will the core signal MOVE on the locally-available model?** <yes/no/unknown —
  e.g. "NO: the only available frontier API refuses 100% of the harmful prompts
  this safety signal needs, so the signal can't move here">
- **Executable on deployed setup**: YES / NO / CONDITIONAL (condition: <...>)

**Novelty bet**: <what makes this not a re-measurement>

> A candidate whose core signal cannot move on the locally-runnable model is
> already dead — drop or pivot it here, before it reaches the signal-de-risk
> gate (engineer/idea-feasibility-derisk) at the end of research.

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
- ❌ Skip Step 3's "measured-but-unexplained" filter — purely
  speculative ideas (no measured phenomenon to ground them) fail
  kill-argument immediately
- ❌ Use only one literature source — confirmation bias by source
  bubble. Three independent sources minimum.

## Output contract

Writes `research/IDEA_CANDIDATES.md` ranked by novelty × tractability.
This is the source of truth for the next skill (`idea-creator`) and
must be present before any experiment plan is written.
