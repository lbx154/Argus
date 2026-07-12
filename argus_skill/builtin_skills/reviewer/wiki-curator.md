---
name: Wiki Curator
description: At mission close, synthesize new pages/* cards from this mission's freshly-appended sources/*, optionally promote existing cards, and regenerate queries/*. Reviewer-only -- engineer never invokes this.
category: research-wiki
version: 1
created_at: 2026-06-04T00:00:00+00:00
---

# Wiki Curator -- turn this mission's evidence into wiki judgments

> Sibling of `argus-reviewer-role.md`. Run as part of the reviewer's
> per-mission pass. See `docs/IDEA_WIKI_DESIGN.md` for the full design.

## When to invoke

- A mission has just completed and the reviewer has issued (or is about
  to issue) its verdict.
- The project has a wiki: `.autors/<project>/wiki/` exists.

If the wiki does not exist, skip this skill entirely. Do NOT initialize
one; bootstrap is an explicit operator decision via
`argus-skill wiki init`.

## Inputs

- Newly written files under `.autors/<project>/wiki/sources/` this
  mission: `papers/`, `repos/`, `runs/`.
- Existing `.autors/<project>/wiki/pages/` technique, conflict, and
  pattern cards.
- The mission transcript and your own verdict.

## Workflow

### Step 0 -- respect the write boundary

You never import `WikiStore`, construct `PageCard`, edit a wiki file, rebuild
indexes, or run validation yourself. The mission-close lifecycle owns those
mechanics:

1. It ingests `paper/refs.bib` and optional `research/LIT_MATRIX.tsv` from the
   explicit mission workdir.
2. It performs the mechanical source-to-scratch lift.
3. It applies your structured `wiki_ops` through `WikiRouter`.
4. `WikiRouter` verifies quoted evidence against immutable sources, runs the
   duplicate judge, versions or tombstones pages, emits audit events, and keeps
   indexes valid.

Backfill warnings are isolated wiki-maintenance warnings. They do not change
your mission verdict unless wiki repair is the mission objective.

### Step 1 -- survey what changed

List `sources/papers/`, `sources/repos/`, and `sources/runs/` files
whose `ingested_at` matches this mission's date OR whose `ingested_by`
field references this mission's id.

### Step 2 -- select durable judgments

The wiki is NOT a journal, but the scratch tier exists exactly so the
wiki can grow without overcommitting to judgment. Be liberal with
scratch creation; conservative with candidate/stable promotion.

Do not duplicate the lifecycle's mechanical scratch lift. Emit an operation
only for a judgment the mechanical path cannot make:

- `scratch -> candidate` promotion: this mission found additional
  evidence (a second source supporting the same technique, a run that
  exercises it, etc). Write the `Why now?` reasoning into
  `reviewer_note` so a future reviewer can re-evaluate.
- `candidate -> stable` promotion: at least two independent sources OR
  at least one run with measurable benefit; reviewer is willing to
  certify that the planner may act on this card.
- `pages/conflicts/<slug>.md` creation: this mission encountered two
  sources whose claims are inverted on the same variable, for example
  the engineer's `WIKI-HANDOFF: conflict candidate` note.
- Demotion (`candidate -> scratch`, `stable -> candidate`): new
  evidence undermines the card.

If none of the judgment cases apply this mission, do not force a
candidate/stable. Return no `wiki_ops`; the mechanical scratch lift is enough.

### Step 3 -- emit structured `wiki_ops`

Return proposals only through the reviewer verdict:

```json
{
  "op": "create_page",
  "id": "grpo-asymmetric-clipping",
  "card_type": "technique",
  "title": "Asymmetric clipping in GRPO",
  "status": "scratch",
  "body": "Short project-specific synthesis.",
  "evidence": [
    {
      "source_id": "2406.12345",
      "quote": "An exact verbatim quote from the immutable source",
      "locator": "paper section or source line"
    }
  ],
  "why": "Reusable technique supported by this mission's evidence"
}
```

For `update_page`, return the complete revised page body and exact supporting
quotes. For `retire_page`, return the stable page `id` and a one-clause `why`;
the router creates a reversible tombstone. A quote that is absent or
paraphrased is rejected mechanically. Never work around a rejection by editing
the wiki directly.

## Non-applicability

- Skip entirely if `.autors/<project>/wiki/` does not exist.
- Skip if the mission produced zero new `sources/*` files.
- Do not write `pages/patterns/*` cross-project cards in M0; the
  cross-project detection is M1.
- Do not auto-generate `query_pack.md` prose; that section is reviewer
  hand-authored when the wiki has enough material to summarize.
