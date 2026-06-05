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

### Step 0 -- backfill from engineer-produced lit artifacts

In practice the engineer often uses codex's native web search and writes
`paper/refs.bib` plus optional `research/LIT_MATRIX.tsv` directly,
without invoking the four named ingestion skills that have the per-skill
wiki hook. The result is that `sources/papers/` stays at 0 even though
the engineer consulted real papers.

Before doing any synthesis, backfill the wiki from whatever literature
artifacts the engineer produced this mission:

```python
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.ingest import ingest_refs_bib, ingest_lit_matrix

wiki_root = Path(".autors/<project>/wiki")
store = WikiStore(wiki_root)

refs_bib = Path("paper/refs.bib")
if refs_bib.exists():
    written = ingest_refs_bib(
        store,
        bib_path=refs_bib,
        ingested_by=f"wiki-curator@mission-{mission_id}",
    )
    # written is a list of newly created source paths; already-present
    # sources are silently skipped because sources are immutable.

lit_matrix = Path("research/LIT_MATRIX.tsv")
if lit_matrix.exists():
    ingest_lit_matrix(store, tsv_path=lit_matrix)
```

This converts each BibTeX entry into an immutable
`sources/papers/<key>.md` card with the verbatim BibTeX stanza as body.
The `relevance_to_*` column of `LIT_MATRIX.tsv`, if present, is appended
to the matching source body as a provenance line. No judgment is added at
this step; the BibTeX entry is a fact, not a claim about importance.
Synthesis into `pages/techniques/*` remains your job in Steps 1-2.

Skip Step 0 silently if neither file exists.

### Step 1 -- survey what changed

List `sources/papers/`, `sources/repos/`, and `sources/runs/` files
whose `ingested_at` matches this mission's date OR whose `ingested_by`
field references this mission's id.

### Step 2 -- mechanical scratch lift + selective candidate promotion

The wiki is NOT a journal, but the scratch tier exists exactly so the
wiki can grow without overcommitting to judgment. Be liberal with
scratch creation; conservative with candidate/stable promotion.

**Mechanical (always do this)**:

For each NEWLY added source this mission (from Step 1):

- `sources/papers/<key>.md` -> create or refresh
  `pages/techniques/<key>.md` with:
    - `status: scratch`
    - `title`: copied from the source title
    - `sources: ["papers/<key>.md"]`
    - `tags`: best-effort 1-3 tags from controlled vocab
      (`.autors/<project>/wiki/data/tags.yaml`); empty list if unclear
    - `reviewer_note`: the `relevance:` line from the source body
      (M0.1 ingest_lit_matrix appends it), or empty
    - `confidence: low`
    - `created_at: <today>`, `last_reviewed_at: <today>`
- `sources/runs/<run-id>.md` with `outcome=failure` and a non-empty
  `failure_signature` that matches the signature of a prior run in
  this project -> create or refresh `pages/patterns/<signature>.md`
  with `status: scratch`, `related_runs` listing both run IDs.

If a scratch page for this source already exists, leave it alone
(scratch is the agent's first guess; do not overwrite it mechanically
on every mission).

**Judgment-required (do only when justified)**:

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
candidate/stable; the mechanical scratch lift above is enough.

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
