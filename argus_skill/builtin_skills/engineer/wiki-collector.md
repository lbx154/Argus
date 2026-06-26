---
name: Wiki Collector
description: Autonomously refresh the project wiki by deriving 5-10 search queries from project state, running them against arxiv/semantic-scholar/github, and writing new findings to sources/papers/ and sources/repos/. Run only when planner has explicitly scheduled a wiki_collect mission. Engineer-only; reviewer's wiki-curator handles promotion.
category: wiki
version: 1
created_at: 2026-06-05T00:00:00+00:00
---

# Wiki Collector -- derive queries from project state, ingest sources

## When to invoke

The planner schedules a `wiki_collect` mission when:
- `.autors/<project>/wiki/` exists
- Backlog has been empty for a non-trivial time
- The bot_state cooldown (default 12h) has elapsed since the last collect

Do NOT invoke this skill outside a planner-scheduled wiki_collect mission.

## Workflow

### Step 1 -- derive 5-10 search queries from project state

Read in this order; each item should be short:

- `project.md`
- `AGENTS.md` and any top-level `*goal*.md`
- The matched special prompts at `$ARGUS_SKILL_SPECIAL_PROMPTS_DIR`, or
  `~/.argus-skill/special_prompts/`
- `research/PIPELINE_STATE.json` if it exists
- The current `--objective` from mission context
- `.autors/<project>/wiki/data/tags.yaml` for the controlled vocab
- `.autors/<project>/wiki/queries/by-tag.md` to see what is already covered

From these, derive 5-10 short search queries that:

- Are concrete enough to return useful arxiv / repo hits. Not
  "improve LLM reasoning"; yes "asymmetric clipping GRPO visual editing".
- Cross-product methods and failure modes seen in `reports/` and `diagnosis/`.
- Tilt toward 2025-2026 work.
- Avoid topics already heavily covered in `queries/by-tag.md`.

Write the derived queries to mission scratch so the reviewer can see
what you searched for.

### Step 2 -- run the searches and ingest

For each query, use whichever of these tools is available: codex native
web search via `--search` if enabled, or the existing
`arxiv-paper-search` / `semantic-scholar-search` skill behavior.

- arxiv: last 18 months, ML/CL/AI categories
- semantic-scholar: citation graph traversal from any matching paper
- GitHub: search repos by topic, sort by stars times recency

For each hit, write an immutable source via the wiki helpers:

```python
from datetime import date
from pathlib import Path
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.schema import SourcePaper, SourceRepo
from argus_skill.wiki.ingest import canonical_paper_id

store = WikiStore(Path(".autors/<project>/wiki"))

# Paper hit
paper_stem = canonical_paper_id(url=arxiv_url, doi=doi_or_none, key=paper_key)
src = SourcePaper(
    id=f"papers/{paper_stem}",
    url=arxiv_url,
    title=paper_title,
    ingested_at=date.today(),
    ingested_by=f"wiki-collector@mission-{mission_id}",
    checksum=f"sha256:{abstract_sha256}",
    body=abstract_text,
)
try:
    store.write_source(src)
except FileExistsError:
    pass  # already ingested; sources are immutable

# Repo hit
repo_src = SourceRepo(
    id=f"repos/{owner}__{repo}",
    url=repo_url,
    title=f"{owner}/{repo} -- {short_description}",
    ingested_at=date.today(),
    ingested_by=f"wiki-collector@mission-{mission_id}",
    checksum=f"sha256:{readme_sha256_or_url_hash}",
    body=readme_excerpt_or_url,  # short: keep under 2 KB
)
try:
    store.write_source(repo_src)
except FileExistsError:
    pass
```

### Step 3 -- update bot_state

At the end of the mission, regardless of outcome, update the cooldown
state:

```python
from datetime import datetime, timezone
from pathlib import Path
from argus_skill.wiki.bot_state import load_bot_state, save_bot_state

path = Path(".autors/<project>/wiki/data/bot_state.json")
state = load_bot_state(path)
state.last_attempted_at = datetime.now(timezone.utc)
state.last_query_seed = "; ".join(queries)  # for next-time diversity
if hit_count == 0:
    state.consecutive_failures += 1
else:
    state.last_collected_at = state.last_attempted_at
    state.consecutive_failures = 0
save_bot_state(path, state)
```

### Step 4 -- short reviewer-facing summary

Output a short note in your final mission summary:

- queries used
- N new papers ingested / M skipped as duplicates
- K new repos ingested
- any noteworthy hits, in 1-2 sentences each, the reviewer might want
  to consider promoting to candidate

Do NOT write any `pages/*` cards yourself. Promotion is the reviewer's
wiki-curator's job. Step 2 mechanical lift will turn each new source
into a scratch page on this same mission's reviewer pass.

## Hard rules

- Stay under the per-mission token budget. If a paper's abstract is
  large, truncate to about 2 KB before storing in source body.
- Do NOT fabricate arxiv IDs or URLs. If a search returns no results,
  record that in the summary and move on.
- Sources are immutable: re-ingestion silently skips. Never overwrite.
- Cooldown: this skill should not be invoked more than once per 12
  hours by the planner.
