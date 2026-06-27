---
name: Deep Research via API
description: "RESEARCH-stage literature playbook — build LITERATURE_GROUNDING from REAL `curl` queries to public arXiv + Crossref APIs (no key needed), GPT-Researcher style: fan out ≥5 sub-queries, fetch every source, source-track each paper, then recurse for depth. HARD BAN on writing literature entries from model knowledge: every paper must come from a real curl call recorded in the execution log. Use for any literature survey, arxiv/Crossref search, deep research, related-work discovery, or building LITERATURE_GROUNDING.json / refs.bib."
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
trustworthy evidence is a source you actually fetched this run.

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
   from what you "know" about a paper. Every entry must trace to a real
   `curl` call to arXiv or Crossref executed **this run**.
2. **No fabricated provenance.** Do NOT write `"queried"`, `"searched"`,
   `"retrieved from"`, `"official scholarly sources"`, or any equivalent into
   ANY file's `metadata`/prose unless the matching real `curl` command appears
   in your execution log. A claimed query with no command is a process-audit
   blocker.
3. **No memory-filled fields.** `abstract`, `authors`, `year`, `venue`,
   `arxiv_id`, and `doi` must be COPIED from the API response, never recalled.
   If a field is not in the response, leave it empty — do not invent it.
4. The reviewer runs `engineer-process-audit` and greps your execution log for
   `curl` against `export.arxiv.org` / `api.crossref.org`. Zero real calls, or
   entries that don't match any logged response, → the research stage is
   BLOCKED and you redo it with real curl evidence.

## Two retrieval channels — both real, both auditable, use BOTH for depth

1. **`curl` to public APIs (the systematic, auditable deep-dive)** — arXiv +
   Crossref work with no key behind the proxy. This is the auditable core: the
   reviewer greps your execution log for these calls. Use curl for systematic
   arXiv/Crossref fan-out and timeline construction.
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
or on OpenReview — places where curl-on-arXiv is blind. For each `web_search`
hit you keep, record its real URL in `retrieved_via` exactly like a curl source.

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

## GPT-Researcher loop — fan-out → fetch → track → recurse

### 1. Fan out (breadth ≥ 5)

Decompose the research objective into **at least 5 distinct sub-queries from
different angles**, not five rewordings of one. Cover, at minimum:

1. **Core task / problem** (e.g. "tool-use agent benchmark")
2. **Proposed method family** (e.g. "retrieval augmented tool selection")
3. **Named benchmarks / datasets** in the area (e.g. "API-Bank", "ToolBench")
4. **Baselines / prior systems** you'll compare against
5. **Evaluation / metric / failure-mode** angle (e.g. "hallucinated tool call evaluation")

Run **each** sub-query against **both** arXiv and Crossref — that is ≥10 real
curl calls in the first round alone. Save raw responses (e.g.
`research/_search/q1_arxiv.xml`, `research/_search/q1_crossref.json`) so each
entry is independently re-checkable.

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
  "source": "arxiv",
  "relevance": "primary tool-use benchmark; baseline for our eval"
}
```

Required per entry: `url` (arXiv `abs` link or `https://doi.org/<DOI>`),
`retrieved_via` (which curl found it — query + round), and a real `abstract`
excerpt drawn from the API payload. An entry missing any of these is treated as
recalled-from-memory and must be removed or re-fetched.

### 4. Recurse for depth (≥ 2 layers)

GPT Researcher's depth dimension: after round 1, mine the retrieved abstracts
for **high-frequency themes, method names, and recurring authors**, then
generate a **second round of sub-queries** targeting those, and curl them for
real. Repeat for at least a second layer (deeper if the topic is broad). The
second round is what turns a flat keyword list into a grounded survey: it finds
the papers the obvious queries miss.

### 5. Aggregate, dedup, cite

Merge rounds, dedup by `arxiv_id`/`doi` (keep the richest record), and keep the
literature matrix (`research/LIT_MATRIX.tsv`) and `paper/refs.bib` in sync with
the grounded entries. Every BibTeX key must correspond to a real retrieved
source. Record the run in `research/SOURCE_DISCOVERY.md`: list each sub-query,
which API answered it, how many hits, and how many were kept — so the trail is
auditable end to end.

## Honest metadata

If you write a `metadata` block, it must be TRUE and specific:

```json
"metadata": {
  "retrieval_method": "real curl to export.arxiv.org + api.crossref.org",
  "sub_queries": 7,
  "rounds": 2,
  "raw_responses_dir": "research/_search/",
  "queried_from_memory": false
}
```

Never write `"Queried official scholarly sources"` as a decorative claim. Either
the curl commands are in your log, or the claim is false.

## Definition of done

- ≥5 distinct sub-queries, each curled against arXiv AND Crossref (≥10 first-round calls).
- ≥2 retrieval rounds (depth), the second derived from round-1 results.
- Every `LITERATURE_GROUNDING.json` entry has `url`, `retrieved_via`, and a real `abstract` excerpt.
- Raw responses saved under `research/_search/`; `SOURCE_DISCOVERY.md` logs the query trail.
- No entry, abstract, or metadata claim originates from model knowledge.

## Integration

- Runs in the **research** stage alongside `arxiv-paper-search.md` and
  `semantic-scholar-search.md`; this skill is the *real-search discipline* those
  searches must obey before anything lands in `LITERATURE_GROUNDING.json`.
- Feeds `research/LITERATURE_GROUNDING.json`, `research/LIT_MATRIX.tsv`,
  `research/SOURCE_DISCOVERY.md`, and `paper/refs.bib`.
- The reviewer's research checklist audits this with `engineer-process-audit`:
  it greps the execution log for real `curl` calls and spot-curls a couple of
  cited urls/DOIs to confirm they exist.
