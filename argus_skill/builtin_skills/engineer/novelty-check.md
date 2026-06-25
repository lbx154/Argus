---
name: Novelty Check
description: Verify that a proposed method/idea has not already been done in recent literature. Extracts 3-5 core technical claims, searches arXiv / Semantic Scholar / OpenAlex per claim, and returns a verdict per claim with citations. Catches "I thought this was novel but it's Smith et al. 2024" before committing experiment budget.
category: paper-ideation
version: 1
author_model: gpt-5.5
created_at: 2026-06-01T00:00:00+00:00
---

# Novelty Check

> Adapted from ARIS `novelty-check` skill (MIT, © 2026 wanshuiyin).

The fastest way to kill a paper is to spend a month on an experiment
that was published 3 months ago by someone else. This skill runs that
check **before** the experiment plan locks in.

## When to invoke

After `idea-discovery` produces IDEA_CANDIDATES.md and before
`idea-creator` spends pilot budget. Also: any time the planner /
author is about to commit to a new method during the research
stage.

## Workflow

### Phase A — extract core claims

Reviewer agent reads the proposed idea and extracts 3-5 **core
technical claims** the idea would need to be novel:

- What is the method?
- What problem does it solve?
- What is the mechanism / key insight?
- What's measured to validate it?
- What's the headline number / comparison?

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

If ≥1 core claim is `done` (fully published by someone else), the
idea **fails novelty-check** and pivots. The reviewer (not the
harness) rules.

Output: `research/NOVELTY_CHECK.md` with the per-claim verdict +
recommended pivot if needed.

## Anti-patterns

- ❌ Single-keyword search — papers rarely use your exact phrasing
- ❌ Search only the last 6 months — a 2-year-old paper still kills
  your novelty claim
- ❌ Treat "I haven't read this" as "this isn't published" — the
  literature scan is the source of truth, not your reading list
- ❌ Run novelty-check AFTER the experiment — it's a gate before
  budget, not a post-hoc cover. By then it's too late

## Integration

Run before `idea-creator` Step 2 (pilot design). A claim flagged
`done` should not enter the pilot list.

## Wiki side-effect (parasitic auto-collection)

If `.autors/<project>/wiki/` exists for the current project, every paper
or repo this skill ingests/sees MUST also be appended as an immutable
source file. Engineer writes facts only -- never judgments.

```python
from datetime import date
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.schema import SourcePaper

wiki_root = Path(".autors") / "<project>" / "wiki"
if wiki_root.exists():
    store = WikiStore(wiki_root)
    src = SourcePaper(
        id=f"papers/{arxiv_id}",
        url=arxiv_url,
        title=paper_title,
        ingested_at=date.today(),
        ingested_by=f"novelty-check@mission-{mission_id}",
        checksum=f"sha256:{abstract_sha256}",
        body=abstract_text,  # verbatim; no opinions
    )
    try:
        store.write_source(src)
    except FileExistsError:
        pass  # already ingested by an earlier mission -- that is fine
```

Notes:
- Sources are immutable. If a paper was ingested before, skip it.
- The body is the verbatim abstract / README excerpt. Do NOT summarize
  or editorialize -- that is the reviewer's job in `wiki-curator`.
- This is best-effort and must NOT fail the mission if the wiki helper
  raises. Catch and log.

### Conflict hand-off

When Phase B finds two sources whose claims are inverted on the same
variable, emit a short note to the reviewer in your mission output:

```text
WIKI-HANDOFF: conflict candidate
  - source A: papers/<id-a>.md -- claim X
  - source B: papers/<id-b>.md -- claim not-X
  - conflict variable: <variable name>
```

The reviewer's `wiki-curator` skill will turn this into a
`pages/conflicts/*.md` card.
