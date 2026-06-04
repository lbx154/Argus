---
name: Wiki Curator
description: At mission close, synthesize new pages/* cards from this mission's freshly-appended sources/*, optionally promote existing cards, and regenerate queries/*. Reviewer-only -- engineer never invokes this.
category: research-wiki
version: 1
scientist_model: gpt-5.5
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

### Step 1 -- survey what changed

List `sources/papers/`, `sources/repos/`, and `sources/runs/` files
whose `ingested_at` matches this mission's date OR whose `ingested_by`
field references this mission's id.

### Step 2 -- decide whether judgment is needed

For each new source, ask:

- Is this an external technique that might transfer beyond this mission?
  If yes, it may deserve a `pages/techniques/*.md` card.
- Does this contradict another source already in the wiki, or a claim
  the engineer relied on this mission? If yes, write or update a
  `pages/conflicts/*.md` card.
- Does the new RunCard's `failure_signature` match an earlier RunCard
  in this project's `sources/runs/`? If yes, write or update a
  `pages/patterns/*.md` card.

If none of the above is true, write nothing. The wiki is not a log;
silence is correct when there is no judgment to add.

### Step 3 -- write or update pages

Use the Python helper:

```python
from datetime import date
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.schema import PageCard

store = WikiStore(Path(".autors/<project>/wiki"))
card = PageCard(
    id="tech-grpo-asym-clip-2026-06-04",
    type="technique",
    status="scratch",  # new cards start at scratch
    title="Asymmetric clipping in GRPO",
    tags=["grpo", "clipping"],
    sources=["papers/2406.12345.md"],  # paths under sources/
    related_runs=[],
    related_projects=[],
    confidence="low",
    revisit_after=date(2026, 9, 4),
    created_at=date.today(),
    last_reviewed_at=date.today(),
    reviewer_note=(
        "Two recent papers use asymmetric clipping; worth testing on "
        "the next training run."
    ),
    body="short prose",
)
store.write_page(card)
```

### Step 4 -- consider promoting existing cards

For each `pages/*` card touched by this mission's evidence:

- `scratch -> candidate`: card has structured fields, sources resolve,
  and the reviewer has now read it. Update `last_reviewed_at`; set
  `status: candidate`.
- `candidate -> stable`: at least two independent sources, or at least
  one run with `outcome=success` or `outcome=partial` referencing the
  card, support it. Reviewer certifies. Update `last_reviewed_at`; set
  `status: stable`.

Demotion is also allowed if new evidence undermines a card.

### Step 5 -- fill in RunCard prose

For the RunCard written by the engineer at mission close
(`sources/runs/<mission-id>.md`):

- The engineer leaves `suspected_cause` and `next_action` empty.
- Open the file, parse it, fill in those two prose fields based on the
  mission transcript and your verdict.
- Write it back. RunCards are a documented exception to source
  immutability for this single reviewer pass; do not edit later.

### Step 6 -- regenerate indexes

```python
from argus_skill.wiki.index import rebuild_indexes
rebuild_indexes(store)
```

### Step 7 -- validate

```python
from argus_skill.wiki.validate import validate_wiki
validate_wiki(store)
```

If validation raises, the curator pass has produced a broken wiki. Fix
in place; do not commit a broken tree.

## Non-applicability

- Skip entirely if `.autors/<project>/wiki/` does not exist.
- Skip if the mission produced zero new `sources/*` files.
- Do not write `pages/patterns/*` cross-project cards in M0; the
  cross-project detection is M1.
- Do not auto-generate `query_pack.md` prose; that section is reviewer
  hand-authored when the wiki has enough material to summarize.
