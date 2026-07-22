# Idea Wiki — design

> Supersedes the earlier "ResearchWiki + Humanize Integration" draft. The
> Humanize half is dropped (see Appendix A). What remains is a focused design
> for a per-project, KernelWiki-shaped **idea wiki** that accumulates research
> inspiration as a side-effect of normal missions and feeds it back into the
> planner.

## Purpose

Argus already has `idea-discovery`, `idea-creator`, `novelty-check`, and
`research-ideation` skills. They all share one property: **each invocation is
single-shot inside one mission**. The literature scan, the cluster of unexplained
phenomena, the conflict noticed by `novelty-check`, the candidate killed by
`kill-argument` — all of it evaporates when the mission ends.

The idea wiki fixes that. It is a **persistent, per-project, schema-driven
ledger** of three kinds of research inspiration that benefit from cross-mission
accumulation:

| Card type | What it captures | Why it can't be done one-shot |
|---|---|---|
| **technique-to-watch** | An external trick / loss / sampling strategy that might transfer to other work | Needs longitudinal tracking ("GRPO variants seen over 6 months") |
| **contradiction** | Two papers / repos claiming opposite things about the same variable | Single-shot search clusters; doesn't pit clusters against each other |
| **cross-project pattern** | A failure or motif seen across ≥2 of this project's RunCards (or across sibling projects) | Requires looking at multiple missions' outputs |

The wiki is explicitly **not** a literature dump and **not** a replacement for
`idea-discovery`'s per-mission gap search.

## Non-goals

- Do not auto-ingest broad web search into Stable.
- Do not embed Humanize's hook-managed RLCR runtime in Argus. See Appendix A.
- Do not let the harness score "which past card is relevant" (consistent with
  Argus's existing recency-only memory model — relevance is the agent's job).
- Do not duplicate Argus's existing skill-lifecycle store
  (`argus_skill/skills/lifecycle.py`, `pending_lessons/`). The wiki produces
  *inspiration*; the lifecycle store produces *verified executable skills*.
  A wiki card may eventually nominate a SkillCandidate, but the lifecycle
  store is downstream and remains the source of truth for live skills.
- Do not auto-discover cross-project links via harness heuristics. A
  cross-project pattern card is written by the reviewer of the project that
  noticed it, and references the other project by path.
- Do not start with an `idle-refresh` mission type. Auto-collection is purely
  parasitic on existing missions; idle proposals come from *querying* the
  accumulated wiki, not from running new crawls.

## Architecture — KernelWiki-shaped, per project

The daemon runtime initializes this tree on the first SkillLoop mission by
default (disable with `ARGUS_SKILL_AUTO_INIT_WIKI=0`). Direct library callers
remain opt-in through `SkillLoopConfig.auto_init_wiki`.

```text
.autors/<project>/wiki/
├── sources/                   Layer 1: raw immutable evidence
│   ├── papers/<arxiv-id>.md
│   ├── repos/<owner__repo>.md
│   └── runs/<run-id>.md
├── pages/                     Layer 2: synthesized judgment cards
│   ├── techniques/<slug>.md
│   ├── conflicts/<slug>.md
│   └── patterns/<slug>.md
├── queries/                   Layer 3: scripts/index.py output, never hand-edited
│   ├── by-status.md
│   ├── by-tag.md
│   ├── stale-watchlist.md
│   └── open-contradictions.md
├── data/
│   ├── schema.yaml            field definitions per card type
│   ├── tags.yaml              controlled vocab (small at first)
│   └── refresh-cutoff.yaml    "last full re-index" timestamp
├── scripts/
│   ├── validate.py            frontmatter + link integrity
│   └── index.py               regenerates queries/ from frontmatter
└── query_pack.md              planner/engineer entry-point summary
```

Three separations matter and they mirror KernelWiki exactly:

1. **Fact vs judgment** — `sources/` is append-only, immutable, no opinions.
   `pages/` carries judgment (which technique matters, which papers conflict)
   and can be revised.
2. **Judgment vs index** — `pages/` is the human/agent-authored layer.
   `queries/` is mechanically regenerated; never the source of truth.
3. **Status as frontmatter, not directory** — promotion (Scratch → Candidate
   → Stable) is a frontmatter field on each `pages/*` card. No file moves,
   no broken links on promotion.

### Frontmatter — pages/*.md

```yaml
---
id: tech-grpo-clip-asym-2026-06-04
type: technique               # technique | conflict | pattern
status: scratch               # scratch | candidate | stable
title: "Asymmetric clipping in GRPO for visual editing"
tags: [grpo, clipping, visual-editing]
sources:
  - papers/2406.12345.md
  - papers/2503.09988.md
related_runs: []              # for patterns: list of sources/runs/*.md
related_projects: []          # for cross-project patterns: list of paths
confidence: low               # low | med | high
revisit_after: 2026-09-04     # for techniques on the watch list
created_at: 2026-06-04
last_reviewed_at: 2026-06-04
reviewer_note: |
  Reviewer-authored: why this matters now, what would kill the
  bet, what the minimum experiment to verify is.
---

<free-form body>
```

The body is short prose — the structured fields are what `scripts/index.py`
queries. `validate.py` enforces required fields per `type`.

### Frontmatter — sources/*.md

```yaml
---
id: papers/2406.12345
url: https://arxiv.org/abs/2406.12345
title: "..."
ingested_at: 2026-06-04
ingested_by: paper-ingestion@mission-abc123
checksum: sha256:...
---

<verbatim abstract or extracted summary; no opinions>
```

Sources are never edited after creation. If a source is wrong, write a new
source with a corrected version and let the page that references it move to
the new ID.

## Data flow — parasitic auto-collection

There is no crawler. The wiki fills up only as a side effect of missions that
were already going to run.

```text
mission start
  │
  ├─ engineer invokes paper-ingestion / arxiv-paper-search / novelty-check /
  │  semantic-scholar-search (existing skills)
  │     └─ those skills append immutable .md files into sources/papers/
  │        and sources/repos/ (no judgment, just facts)
  │
  ├─ engineer (or its sub-tools) drops the mission's RunCard into
  │  sources/runs/<run-id>.md at mission completion (see RunCard schema
  │  below — this is the one new artifact the engineer must produce)
  │
  └─ mission completes
        │
        └─ reviewer runs the new wiki-curator skill:
              1. Read newly appended sources/ files for this mission.
              2. Decide whether any of them warrant a pages/* card.
                 - Only synthesize a card when there is actual judgment to
                   add: a technique looks portable, two sources disagree,
                   a run echoes a prior run's failure signature.
              3. Write or update pages/*.md with status: scratch.
              4. For any existing scratch / candidate cards touched by
                 this mission's evidence, optionally promote one step:
                   scratch → candidate (structured + sourced)
                   candidate → stable  (reviewer-certified, evidence
                                       across ≥2 sources or ≥1 run)
              5. Run scripts/index.py to regenerate queries/.
```

Bootstrap, source ingestion, scratch lift, index rebuild, and promotion are
deterministic. Reviewer-authored `wiki_ops` ride the existing review verdict;
an independence check for a new page in a non-empty wiki, or explicit automatic
compaction, may issue an additional metered call. Those calls share the same
mission ledger, atomic reservation, and provider fence as the core task.

## Data flow — active proposal (planner idle path)

Auto-proposal is **not** a separate crawler. It is the planner reading
`queries/stale-watchlist.md` and `queries/open-contradictions.md` when its
backlog goes empty in continuous mode.

```text
planner: backlog empty?
  │
  ├─ no  → schedule next backlog item, done
  │
  └─ yes → read .autors/<project>/wiki/queries/stale-watchlist.md
              + queries/open-contradictions.md
              + queries/by-status.md (stable count, age distribution)
            │
            └─ choose: do nothing, or enqueue an idea-creator mission seeded
               with the top stale technique / unresolved contradiction.
               The choice is the planner's; the harness does not score.
```

`stale-watchlist.md` is just: every technique card with status≥candidate and
`revisit_after < today`, sorted by age. `open-contradictions.md` is every
conflict card with status≥candidate and no `resolved_by_run` field set.
Pure frontmatter filters, no LLM.

## RunCard — the only new artifact engineer must produce

This is the bridge that makes cross-project pattern cards possible.

```yaml
---
id: runs/2026-06-04-mission-abc123
mission_id: abc123
git_commit: d6f8520
project: <project-name>
config_path: experiments/run-0042/config.yaml
dataset: <name>
metrics:
  train_loss_final: 0.182
  eval_score: 0.41
artifacts:
  curves: experiments/run-0042/curves.png
  sample_grid: experiments/run-0042/grid.png
outcome: failure                # success | partial | failure
failure_signature: nan-after-step-12k-with-grpo-asym-clip
suspected_cause: |
  Free-form text; reviewer authored.
next_action: |
  Free-form text; reviewer authored.
---
```

`failure_signature` is what makes cross-project pattern detection possible
*later*. In M0 the field is just written; nobody reads it across projects.
M1 will add a wiki-curator pass that greps `failure_signature` across
`.autors/*/wiki/sources/runs/` and, on a match, writes/updates a
`pages/patterns/*` card. Writing the field now is cheap and forward-compatible.

Producing the RunCard is a small append at mission close. It does not require
the engineer to summarize the whole experiment — `suspected_cause` and
`next_action` are filled by the reviewer.

## Skill changes — what the existing skills must do differently

This is the smallest set of changes that makes the wiki real.

### Engineer-side (existing skills, prompt-only changes)

- `paper-ingestion.md`: at the end of every ingestion, append a frontmatter-
  stamped `sources/papers/<arxiv-id>.md`. No judgment, just facts.
- `arxiv-paper-search.md`, `semantic-scholar-search.md`: when the top-K
  abstracts are pulled, append each as a thin `sources/papers/<arxiv-id>.md`
  (title + abstract + checksum). Cheap.
- `novelty-check.md`: when Phase B finds two sources whose claims are
  inverted, append both as sources/papers/ (if not already) and emit a
  short reviewer hand-off note ("these two disagree on X") to the mission
  scratch — the reviewer turns it into a `pages/conflicts/*.md`.
- Engineer mission close: write `sources/runs/<run-id>.md` with the metrics
  block filled in. Reviewer fills the prose later.

### Reviewer-side (new skill)

`argus_skill/builtin_skills/reviewer/wiki-curator.md`. Invoked as part of the
reviewer's existing per-mission pass. Responsibilities:

1. Synthesize new `pages/*.md` from this mission's appended sources, only when
   judgment is needed.
2. Touch existing pages: promote status if new evidence justifies it.
3. Fill in `suspected_cause` / `next_action` on the RunCard.
4. Run `scripts/index.py` (or call into `argus_skill.wiki.index`).

### Planner-side (existing planner, prompt-only changes)

- On idle (backlog empty), read `query_pack.md` + `queries/stale-watchlist.md`
  + `queries/open-contradictions.md` as part of the planner context.
- Decide whether to enqueue an `idea-creator` mission seeded by the top item.
  The choice remains the planner's; the harness does not threshold or score.

## Python package — minimal helper, not a runtime

```text
argus_skill/wiki/
├── __init__.py
├── schema.py          dataclasses for SourceFile / PageCard / RunCard
├── store.py           read/write with consistent frontmatter; atomic append
├── index.py           regenerate queries/*.md from frontmatter
└── validate.py        schema + link integrity check
```

What it does **not** do:
- No query planner (queries live in skills, not Python).
- No source triage (engineer skills decide what to ingest).
- No repo dive (out of scope for M0).
- No promotion logic (reviewer authors that; store just persists).
- No relevance scoring (per project philosophy).

## M0 — minimum cuttable slice

1. `argus_skill/wiki/{schema,store,index,validate}.py`.
2. Prompt updates in `paper-ingestion`, `arxiv-paper-search`, `novelty-check`
   to drop `sources/*` files. ~5 skills touched.
3. New `argus_skill/builtin_skills/reviewer/wiki-curator.md`.
4. RunCard emission hook at mission close (engineer side).
5. Planner prompt patch to read `query_pack.md` on idle.
6. One project (pick the most active one) opts in by `mkdir -p
   .autors/<project>/wiki/{sources,pages,queries,data,scripts}` and dropping
   in default `data/schema.yaml` + `data/tags.yaml`.

That's it for M0. No watchlist refresh mission, no cross-project linking, no
SkillCandidate emission. Get the parasitic write loop working on one project
for ~2 weeks before adding more.

## M1 candidates (not in scope yet)

- Cross-project pattern detection across sibling `.autors/*/wiki/sources/runs/`.
- Promotion from a repeatedly-validated technique-to-watch card to a
  `SkillCandidate` that enters the existing `pending_lessons → distilled
  skill` lifecycle.
- A planner mission type `wiki-refresh` for when backlog is empty AND the
  parasitic loop has produced no new scratch in N days.
- Per-project `query_pack.md` regeneration (currently written by reviewer).

## Design principle

```text
sources are facts;
pages are judgments;
queries are mechanical;
the reviewer is the only promoter;
the planner reads, does not crawl.
```

Without the fact/judgment split, the wiki rots. Without reviewer-only
promotion, status drifts. Without keeping the planner read-only, we
reintroduce the harness-decides-relevance pattern that Argus has explicitly
rejected.

---

## Appendix A — Why Humanize is out of scope

The earlier draft proposed borrowing Humanize's plan-generation, AC-style
acceptance criteria, scope-bound goal tracking, independent review, and
BitLesson-style experience capture. On audit, every one of these maps 1:1
onto something Argus already ships:

| Humanize concept | Argus equivalent |
|---|---|
| plan generation from a draft | `planner/planner.py` + `planner_schema.json` |
| AC-style acceptance criteria | `skills/stage_checklists.py` + reviewer verdicts |
| upper/lower scope bounds | stage checklist `done` / `continue` / `blocked` |
| goal tracking across rounds | `life/memory.py` EventJournal + Backlog |
| independent review | L2 reviewer (with shell access) |
| BitLesson-style capture | `skills/lessons.py` + `lifecycle.py` distill/revise/promote/retire |

Embedding Humanize's stop-hooks would create a second nested control loop
on top of `LifeSupervisor` / `SkillLoop` / `SupervisedEngineer` / `Reviewer`
and would duplicate review, duplicate Codex calls, and make mission completion
ambiguous. Adopting Humanize's vocabulary on top of the existing primitives
would create two names for the same thing.

The earlier draft was right to refuse the full hook runtime. This document
takes one further step: it refuses the vocabulary too. If a future need arises
to import some Humanize idea (e.g. BitLesson-style structured failure capture
beyond what `lessons.py` does today), it should be argued in a new doc
explaining *which existing Argus primitive is insufficient and why*, not by
borrowing the framework wholesale.
