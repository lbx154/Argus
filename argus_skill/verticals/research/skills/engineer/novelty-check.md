---
name: "Novelty Check"
description: "Verify that a proposed method/idea has not already been done in recent literature. Extracts 3-5 core technical claims, searches arXiv / Semantic Scholar / OpenAlex per claim, and returns a verdict per claim with citations. Catches \"I thought this was novel but it's Smith et al. 2024\" before committing experiment budget."
---

# Novelty Check

> Adapted from ARIS `novelty-check` skill (MIT, © 2026 wanshuiyin).

The fastest way to kill a paper is to spend a month on an experiment
that was published 3 months ago by someone else. This skill runs that
check **before** the experiment plan locks in.

For publishable/doctoral work, novelty is one part of the ambition standard:
nontrivial technical core, verified originality, claim-relevant formal/causal
grounding, and field-level consequence. A new combination of known parts, new
name, new prompt, or new benchmark setting is not verified originality unless
it creates a mechanism or insight the closest work lacks.

## When to invoke

During each independent route review after `idea-discovery` produces the
candidate and before the one-time selector runs. Selection is read-only: inspect
primary literature and official sources, but do not execute candidate code or
spend pilot/experiment budget. Once one route is selected, novelty checking
cannot reopen idea discovery or selection.

## Workflow

### Phase A — extract core claims

Reviewer agent reads the proposed idea and extracts 3-5 **core
technical claims** the idea would need to be novel:

- What is the method?
- What problem does it solve?
- What is the mechanism / key insight?
- What's measured to validate it?
- What's the headline number / comparison?
- What formal/causal prediction and field-level consequence distinguish it from
  a recombination or local engineering improvement?

### Phase B — per-claim literature search

For each core claim, search **at least 3 of**: arXiv, Semantic
Scholar, OpenAlex, ACL Anthology, OpenReview. For each search:
- Query phrasing in 2-3 paraphrases (different keywords trip
  different result subsets)
- Look at top 30 results
- Pull abstract + one-paragraph TLDR for any whose title/abstract
  matches the claim

### Phase C — verdict per claim

For each claim, the reviewer returns:

```json
{
  "claim": "Use of self-consistency improves arithmetic but degrades code",
  "verdict": "novel" | "partially_done" | "done",
  "closest_prior_work": [
    {"cite": "Wang et al. 2023 (arXiv:2306.xxxx)",
     "what_they_did": "Showed self-consistency on math benchmarks",
     "what_they_did_NOT_do": "Did not test code generation"}
  ],
  "novelty_remaining": "<what part of the claim is still unclaimed>"
}
```

### Phase D — aggregate

If ≥1 core claim is `done` (fully published by someone else), the route review
records that fatal concern for the selector. The reviewer (not the harness)
rules, and the selector still compares all twelve completed route/review pairs
once.

Output: `research/NOVELTY_CHECK.md` with the per-claim verdict +
the surviving novelty boundary.

## Anti-patterns

- ❌ Single-keyword search — papers rarely use your exact phrasing
- ❌ Search only the last 6 months — a 2-year-old paper still kills
  your novelty claim
- ❌ Treat "I haven't read this" as "this isn't published" — the
  literature scan is the source of truth, not your reading list
- ❌ Run novelty-check AFTER the experiment — it's a gate before
  budget, not a post-hoc cover. By then it's too late

## Integration

Run inside route review before the one-time `idea-creator` selection. A claim
flagged `done` must be visible to the selector; it cannot trigger a second
portfolio after selection.

## Optional Wiki retention

A literature search does not automatically create Wiki content. If the evidence
changes durable declarative knowledge, read the Wiki `INDEX.md`, refine or create
one semantically named page with only `title` and `description` frontmatter, cite
real URLs in its Markdown body, and update INDEX.md. Otherwise make no Wiki edit.

### Conflict hand-off

When Phase B finds two sources whose claims are inverted on the same
variable, emit a short note to the reviewer in your mission output:

```text
WIKI-HANDOFF: conflict candidate
  - source A: papers/<id-a>.md -- claim X
  - source B: papers/<id-b>.md -- claim not-X
  - conflict variable: <variable name>
```

The Reviewer may directly turn this into a `pages/conflicts/*.md` card after
checking both immutable sources. There is no structured page-operation or
automatic-promotion channel.
