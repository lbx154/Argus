---
name: Paper Ingestion
description: Signal B (literature) handler for the self-evolve loop. Scans research/PAPERS/ for operator-dropped PDFs / arxiv IDs (or fetches a single one via --arxiv), extracts method, code repo, and evaluation benchmark per paper, dedups proposed methods against existing skills, and writes ranked entries to research/IDEA_CANDIDATES.md so the standard idea-discovery / idea-creator / novelty-check / kill-argument pipeline picks them up. Optional follow-up: if a paper's method is high-novelty AND has a public benchmark, route to mint-skill to wrap the method as a new skill validated against the paper's reported number.
category: self-evolve
version: 1
created_at: 2026-06-01T00:00:00+00:00
---

# Paper Ingestion (Signal B · auto-evolve)

> Argus self-evolve loop, **Signal B (literature)**. Pairs with
> Signal A (`missing_tool_detector`) and Signal C
> (`feedback-parser`). All three feed candidate work into the same
> downstream pipeline (`idea-discovery` → `novelty-check` →
> `kill-argument` → optional `mint-skill`); this skill is the
> structured-ingestion adapter that turns "operator dropped 3
> papers" into IDEA_CANDIDATES.md rows.

## When YOU are invoked

The planner schedules this skill in two situations:

1. **Operator drop**: a new file lands under `research/PAPERS/`
   (PDF, arxiv ID file `*.arxiv-id`, or markdown stub with
   `arxiv_id:` frontmatter). Treat the modification time as the
   ingestion order.
2. **Scheduled scan**: every N planner cycles, scan `research/
   PAPERS/` for any paper not yet present in
   `research/INGESTED_PAPERS.json`.

## Output contract

Write to:

- `research/INGESTED_PAPERS.json` — per-paper record, idempotent.
  Append-only; rows keyed by arxiv_id or content hash.
- `research/IDEA_CANDIDATES.md` — extracted candidate methods,
  one section per candidate, ranked by novelty × tractability ×
  benchmark-availability. Format is the same as the
  `idea-discovery` skill produces so the downstream pipeline
  doesn't care which signal generated the row.
- `research/PAPER_METHOD_GRAPH.json` — extracted method-to-paper
  edges (lets the agent reason about "which paper introduced
  method M, who else compared against it").

## Workflow

### Step 1 — list new papers

```bash
mkdir -p research/PAPERS research/
ls research/PAPERS/ | grep -E '\.(pdf|md|arxiv-id)$' | sort
```

Cross-reference `research/INGESTED_PAPERS.json` (create with
`[]` if missing) to find which are new.

### Step 2 — extract per paper

For each new paper:

1. **Get the source text**
   - PDF: `python -m argus_skill.skills.evidence_chain --project-root .` to
     verify the path exists, then use `pdfplumber` / `pymupdf` to
     extract the text. If the package isn't installed, this
     emits a Signal A missing-tool → mint-skill mission, which is
     **correct expected behaviour** — the loop is self-bootstrapping.
   - arxiv ID: `WebFetch arxiv.org/abs/<id>` for the abstract +
     `WebFetch arxiv.org/pdf/<id>` for the body
   - md stub: read directly

2. **Extract structured fields** (gpt-5.5 via `author` route, ONE
   call per paper, zero shared context with other papers — same
   anti-confirmation-bias as citation-audit):

```json
{
  "paper_id": "<arxiv_id or content-sha>",
  "title": "...",
  "authors": ["..."],
  "year": 2026,
  "venue": "EMNLP 2026 | preprint | ...",
  "method_name": "<short canonical name>",
  "method_summary": "<one-paragraph mechanism explanation>",
  "code_repo": "github.com/... | null",
  "evaluation_benchmarks": [
    {"name": "MT-Bench", "url": "...", "reported_score": 0.82}
  ],
  "claims": [
    "<core claim 1>",
    "..."
  ],
  "anticipated_kill_argument": "<the 50-word rejection a hostile reviewer
  would write — pre-emptive, because we'll feed it to kill-argument later>"
}
```

3. **Append** to `INGESTED_PAPERS.json`.

### Step 3 — dedup against existing skills

```bash
ls argus_builtin_skills/engineer/ | grep -i "<method_name keywords>"
grep -ri "<method_name>" argus_builtin_skills/ | head
```

For each extracted method:
- If a current skill already wraps this method → record
  `existing_skill: <name>` in INGESTED_PAPERS.json and SKIP
- If no skill exists → continue to Step 4

### Step 4 — write candidate entries

For each unique novel method, append a section to
`research/IDEA_CANDIDATES.md` using the canonical idea-discovery
format:

```markdown
## Candidate I-<n>: <method_name> from <paper_id>

**Phenomenon to explain / extend**: <from paper's contribution>

**Hypothesis**: <falsifiable claim, derived from the paper's
mechanism>

**Train-free experiment sketch**:
- Setup: <subset of paper's setup that runs on our budget>
- Falsifier: <what observation refutes the hypothesis>
- Approximate budget: <token + wall-time estimate>

**Novelty bet**: <what specifically makes this not a
re-measurement>

**Source**: <paper_id> · `code_repo: ...` ·
`evaluation_benchmarks: [MT-Bench, ...]`

**Anticipated kill-argument** (we'll feed this to `kill-argument`
later): <pre-emptive 50-word rejection>
```

### Step 5 — (optional) route to mint-skill for novel methods

If a paper's method:
- Is not covered by any existing skill (Step 3)
- Has a public code repo (reproducible)
- Has a measurable benchmark (we can validate against the paper's
  reported number)

→ Write a short journal entry recommending the planner enqueue a
mint-skill mission targeted at "wrap <method_name> as a callable
skill, validate against <benchmark> ± noise of paper's reported
score". The planner decides whether to enqueue (typically: only
if the candidate ranks high in `idea-creator`).

**Do NOT directly enqueue mint-skill here** — that's the planner's
call, and mint-skill missions are also a budget commitment that
must compete with research missions.

## Anti-patterns

- ❌ Reading multiple papers in one reviewer call — confirmation
  bias makes the second paper look like the first. One call per
  paper.
- ❌ Skipping the dedup step — re-extracting a method we already
  wrapped pollutes IDEA_CANDIDATES.md
- ❌ Inventing a "novelty bet" for a paper that's just incremental
  — better to record the paper in INGESTED_PAPERS.json without an
  IDEA_CANDIDATES.md entry. Re-measurement is exactly what the
  reviewer is told to reject (see operator house rules)
- ❌ Auto-minting without going through novelty-check + kill-
  argument first — Step 5 is a recommendation, not an action

## Integration with the rest of argus

- This skill is **agent-side**; the harness doesn't trigger it.
  The planner reads `research/PAPERS/` mtime + IDEA_CANDIDATES.md
  recency and decides when to invoke.
- Output feeds `idea-discovery` / `idea-creator` / `novelty-check`
  via the standard IDEA_CANDIDATES.md format
- F4 evidence_chain validates that any IDEA_CANDIDATES.md row's
  `Source: <paper_id>` cite resolves
- If extraction needs `pdfplumber` or `pymupdf` and they're not
  installed → Signal A handler auto-mints them as a side effect
  (loop self-bootstraps)
- This is Signal B in the 3-signal architecture:
  - Signal A · trajectory: `missing_tool_detector`
  - Signal B · literature: **this skill**
  - Signal C · user feedback: `feedback-parser`
  All three converge on IDEA_CANDIDATES.md / mint-skill
  enqueueing; the downstream pipeline is shared.

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
        ingested_by=f"paper-ingestion@mission-{mission_id}",
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
