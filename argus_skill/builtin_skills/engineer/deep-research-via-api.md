---
name: Deep Research via API
description: "RESEARCH-stage literature playbook — build one canonical LITERATURE_GROUNDING ledger from real primary-source retrieval. Search adaptively until every material claim, nearest competitor, relevant foundation, and contradiction is covered; never optimize for query or paper counts."
category: literature
priority: high
version: 1
created_at: 2026-06-27T00:00:00+00:00
---

# Deep Research via API — real-search literature grounding

This is the **research-stage** literature playbook. Its job is to make the
literature grounding *earned from real retrieval*, not recited from the model's
training memory. Inspired by GPT Researcher: the LLM that "remembers" a paper
will hallucinate its authors, year, venue, and even its arXiv id. The only
trustworthy evidence is a primary source you fetched or a previously
Reviewer-certified cached response whose content still matches its recorded
identity/hash.

## Why this exists (the failure mode it kills)

A capable model can "background" a plausible `LITERATURE_GROUNDING.json` for a
well-known topic — guessing arXiv ids for famous benchmarks (API-Bank,
AgentBench) and writing abstracts from memory — **without ever touching the
network**. The artifact then looks complete and even passes a shallow review.
This is fabrication: the ids drift, the abstracts are paraphrased-from-memory,
and the `metadata` quietly claims *"Queried official scholarly sources"* when no
query ran. Process audit catches it; do not be the engineer it catches.

## ⛔ Non-negotiable prohibitions

1. **No model-knowledge literature.** You may NOT write any
   `LITERATURE_GROUNDING.json` entry, `refs.bib` entry, or related-work claim
   from what you "know" about a paper. Every entry must trace to a real primary
   URL and either a cached retrieved payload or a retrieval performed for the
   unresolved claim. Reuse previously certified cached sources unless a
   concrete conflict or stale dependency requires refetching.
2. **No fabricated provenance.** Do NOT write `"queried"`, `"searched"`,
   `"retrieved from"`, `"official scholarly sources"`, or any equivalent into
   ANY file's `metadata`/prose unless the matching source URL and retrieval
   artifact are recorded in the canonical ledger.
3. **No memory-filled fields.** `abstract`, `authors`, `year`, `venue`,
   `arxiv_id`, and `doi` must be COPIED from the API response, never recalled.
   If a field is not in the response, leave it empty — do not invent it.
4. The Reviewer checks source identity and claim coverage. A missing source,
   fabricated provenance claim, or source that does not support its recorded
   implication blocks the stage; the number of shell commands does not.

## Retrieval channels — choose the source that answers the question

1. **`curl` to public APIs (systematic metadata retrieval)** — arXiv and
   Crossref work with no key behind the proxy. Use them when they are the
   shortest path to the needed source metadata.
2. **codex `web_search` (the breadth channel — via the Responses API, it WORKS)**
   — an earlier note said "Copilot web search is blocked"; that was a mistake
   (it referred to the `--ghc` WebSearch limit, NOT codex's own `web_search`
   tool, which reaches the open web through the Responses API). Use `web_search`
   for exactly what curl-on-arXiv MISSES: conference pages (ICML / ICLR / NeurIPS
   virtual sites + **ACL Anthology**), **OpenReview** submissions, very recent
   work (last ~3 months not yet indexed), **机器之心 / 新智元** trend coverage,
   and "has someone already solved this idea?". `web_search` returns real,
   already-verified URLs (not model memory), so each hit is a real source you
   still spot-check and source-track like a curl hit.

Neither channel is model memory. The arXiv/Crossref `curl` recipes are below;
reach for `web_search` whenever the literature is recent, conference-published,
or on OpenReview — places where curl-on-arXiv is blind. Use one or both channels
according to the coverage gap. For each hit you keep, record its real URL and
cached source artifact.

### arXiv (Atom XML — title / abstract / arxiv id)

```bash
# AND semantics: join terms with +AND+ ; phrases use %22...%22 ; never raw spaces.
curl -sL --max-time 30 \
  "https://export.arxiv.org/api/query?search_query=all:tool+AND+all:benchmark+AND+all:agent&start=0&max_results=10&sortBy=relevance&sortOrder=descending"
# Recent-first within a category:
curl -sL --max-time 30 \
  "https://export.arxiv.org/api/query?search_query=cat:cs.CL+AND+all:%22tool%20learning%22&max_results=10&sortBy=submittedDate&sortOrder=descending"
```

Each `<entry>` gives `<title>`, `<summary>` (the real abstract), `<id>` (the
`http://arxiv.org/abs/XXXX.YYYYY` url), `<published>`, and `<author><name>`.
Pull a clean JSON view with one command:

```bash
curl -sL --max-time 30 \
  "https://export.arxiv.org/api/query?search_query=all:%22API-Bank%22&max_results=5" \
| python -c "import sys,xml.etree.ElementTree as ET; \
ns={'a':'http://www.w3.org/2005/Atom'}; r=ET.fromstring(sys.stdin.read()); \
[print(e.find('a:id',ns).text, '|', e.find('a:title',ns).text.strip().replace('\n',' ')) for e in r.findall('a:entry',ns)]"
```

### Crossref (JSON — title / DOI / abstract / authors / date)

```bash
curl -s --max-time 30 \
  "https://api.crossref.org/works?query=AgentBench+LLM+agent+benchmark&rows=10&select=title,DOI,abstract,author,published,container-title"
# Add a polite mailto so Crossref routes you to the fast pool:
curl -s --max-time 30 \
  "https://api.crossref.org/works?query=tool+use+language+model&rows=10&select=title,DOI,abstract,author,published&mailto=argus-research@example.org"
```

Parse `message.items[]`: `title[0]`, `DOI`, `abstract` (JATS), `author[]`,
`published.date-parts[0][0]` (year). The canonical url is
`https://doi.org/<DOI>`.

> Semantic Scholar (`api.semanticscholar.org`) is an OPTIONAL fallback only — it
> 429-rate-limits without a key. Do not depend on it; arXiv + Crossref are the
> required pair.

## Claim-directed loop — identify gaps → fetch → track → stop when covered

### 1. Identify the claims that need sources

Decompose the research objective into material source questions. Typical angles
include:

1. **Core task / problem** (e.g. "tool-use agent benchmark")
2. **Proposed method family** (e.g. "retrieval augmented tool selection")
3. **Named benchmarks / datasets** in the area (e.g. "API-Bank", "ToolBench")
4. **Baselines / prior systems** you'll compare against
5. **Evaluation / metric / failure-mode** angle (e.g. "hallucinated tool call evaluation")

Use the source channel suited to each question: arXiv for preprints, Crossref
for DOI/venue metadata, ACL Anthology/OpenReview/conference pages for published
work, and official repositories or lab pages for implementation/release facts.
Do not send every query to every API merely to increase a counter. Cache each
retained primary response under `research/_search/` so it can be reused without
another model or network turn.

### 2. Fetch & summarize each source individually

For every relevant hit, read the **returned** abstract (arXiv `<summary>` /
Crossref `abstract`). Summarize from THAT text, one source at a time — never
batch-summarize from memory. Drop hits that the abstract shows are off-topic;
do not keep a paper just because the title looked right.

### 3. Source-track every kept paper

`research/LITERATURE_GROUNDING.json` entries must carry real provenance:

```json
{
  "title": "<copied from API response>",
  "authors": ["<copied>"],
  "year": 2023,
  "venue": "<from Crossref container-title, or 'arXiv preprint'>",
  "arxiv_id": "2304.08244",
  "doi": "10.18653/v1/...",
  "url": "https://arxiv.org/abs/2304.08244",
  "abstract": "<verbatim excerpt from the API response, NOT paraphrased from memory>",
  "retrieved_via": "curl arXiv search_query=all:%22API-Bank%22 (round 1, q3)",
  "raw_response_path": "research/_search/q3_arxiv.xml",
  "source": "arxiv",
  "relevance": "primary tool-use benchmark; baseline for our eval"
}
```

Required per entry: `url` (arXiv `abs` link or `https://doi.org/<DOI>`),
`retrieved_via`, `raw_response_path` (or equivalent cached-source field),
project relevance/implication, and a real `abstract` excerpt drawn from the API
payload. An entry missing any of these is treated as ungrounded and must be
repaired from a real source.

### 4. Recurse only when coverage exposes a real gap

After the first retrieval pass, inspect whether a material premise, nearest
competitor, classic lineage edge, contradictory result, or benchmark origin is
still unsupported. Search again only for those explicit gaps. A narrow topic
may be complete in one pass; a broad or disputed topic may require several.
Depth is a connected argument, not a mandatory number of rounds.

### 5. Aggregate, dedup, cite

Merge retrievals and dedup by `arxiv_id`/`doi` (keep the richest record) in the
canonical ledger. Generate `research/LIT_MATRIX.tsv` from that ledger with the
ledger tool; do not maintain it independently. Every eventual BibTeX key must
correspond to a retained source. Record search questions and unresolved gaps in
ledger metadata only when they help future research decisions.

## Honest metadata

If you write a `metadata` block, it must be TRUE and specific:

```json
"metadata": {
  "retrieval_method": "real curl to export.arxiv.org + api.crossref.org",
  "source_questions": ["nearest competitor", "benchmark origin", "failure boundary"],
  "coverage_gaps": [],
  "raw_responses_dir": "research/_search/",
  "queried_from_memory": false
}
```

Never write `"Queried official scholarly sources"` as a decorative claim. Either
the curl commands are in your log, or the claim is false.

## Definition of done

- Every material research premise, nearest competitor, relevant foundation,
  contradictory result, and benchmark origin is connected to a retained
  primary source, or explicitly marked unresolved.
- Every `LITERATURE_GROUNDING.json` entry has a primary `url`, source provenance,
  and a project-relevant implication.
- Retained raw responses are cached under `research/_search/`.
- `python -m argus_skill.verticals.research.literature_ledger check` passes and
  `... literature_ledger sync` deterministically generates `LIT_MATRIX.tsv`.
- No entry, abstract, or metadata claim originates from model knowledge.

## Integration

- Runs in the **research** stage alongside whichever scholarly search source is
  appropriate. It is the source-integrity discipline before anything lands in
  canonical `research/LITERATURE_GROUNDING.json`.
- The ledger tool generates `research/LIT_MATRIX.tsv`; bibliography generation
  or manuscript prose consumes the canonical ledger rather than another
  independently maintained survey.
- The Reviewer validates the ledger and refetches only missing, contradictory,
  implausible, or disputed sources. It does not grade quality by query count.
