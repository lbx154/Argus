---
name: Deep Research via Source Timeline
description: Depth of research is measured by reconstructing the FIELD'S TIMELINE (founding work → key turning points → current SOTA → open frontier), each node backed by a real fetched source (curl arXiv/Crossref OR codex web_search via the Responses API) — NOT by paper count. Forces real search instead of reciting the model's prior knowledge; reach for web_search for conference/OpenReview/recent work that curl-on-arXiv misses.
category: engineer
version: 1
---

# Deep Research — measured by TIMELINE, not paper count

The standard for "did you actually do deep research" is **not how many papers
you listed** — it is whether you can **reconstruct the development timeline of
the research direction**: who first posed the problem, where it got stuck, who
broke through, where the SOTA is now, and what is still open. A coherent
timeline proves you read and traced the field; a pile of 10 famous papers
(whose arxiv ids the model already memorized) proves nothing.

**If the timeline doesn't connect, you didn't research — you recited.**

## Why count is the wrong metric
Listing API-Bank / AgentBench / SWE-bench with their arxiv ids is trivial: the
model knows them by heart. That is *book-reporting*, not research. Real depth =
you can tell the **lineage**: what came first, what each milestone advanced or
left unsolved, and how the line of work arrived at today's frontier — which is
exactly where your paper plugs in.

## How to do it (timeline-driven, REAL search only)

Two real-search channels — use BOTH:
- **`curl` to public APIs (no key)** — arXiv + Crossref, the systematic auditable
  deep-dive (the reviewer greps your log for these). Recipes below.
- **codex `web_search` (via the Responses API — it WORKS)** — an earlier note
  here said web_search is "unavailable"; that was wrong (it was the `--ghc`
  WebSearch limit, NOT codex's own tool, which reaches the open web through the
  Responses API). Use it for what curl-on-arXiv MISSES: conference pages
  (ICML/ICLR/NeurIPS virtual sites + ACL Anthology), OpenReview, very recent work
  (last ~3 months), and 机器之心 / 新智元 trend coverage. It returns real,
  already-verified URLs — record each in the timeline node's source like a curl
  hit (still spot-check it).

Do **real** retrieval (never model memory). arXiv/Crossref curl recipes:

1. **Fix the direction(s)** from the research objective. Pick the 2–4 lines of
   work the paper actually sits on (e.g. "tool-use agent benchmarks",
   "matched-condition agent evaluation").

2. **Walk the time axis — query oldest AND newest** for each line:
   ```bash
   # arxiv, oldest-first (find the founding work)
   curl -sL "https://export.arxiv.org/api/query?search_query=all:<terms>&sortBy=submittedDate&sortOrder=ascending&max_results=20"
   # arxiv, newest-first (find the current frontier)
   curl -sL "https://export.arxiv.org/api/query?search_query=all:<terms>&sortBy=submittedDate&sortOrder=descending&max_results=20"
   # Crossref, chronological (DOIs + abstracts + dates)
   curl -s "https://api.crossref.org/works?query=<terms>&rows=20&select=title,DOI,abstract,published&sort=published&order=asc"
   ```
   Read the returned titles/abstracts. Re-query with sharper terms you learn
   from the first pass (this is the depth recursion — see step 5).

3. **Build `research/RESEARCH_TIMELINE.md`** along the time axis, not as a flat
   list:
   - **Founding era** — the earliest work that posed this problem / introduced
     the method.
   - **Key turning points** — the papers that changed the direction.
   - **Current SOTA** — the latest, strongest line.
   - **Open frontier** — what is still unsolved; THIS is your paper's entry point.
   - Each node: `YEAR — title — what it advanced / what it left open — real URL
     (arxiv abs or DOI link)`.

4. **Every node's paper must come from a real `curl`** (the call is in your
   execution log). In `research/LITERATURE_GROUNDING.json`, every entry carries
   `year` + `url`/`DOI` + `retrieved_via` (which curl found it) + a real
   `abstract` excerpt copied from the API response — never invented.

5. **Depth recursion (breadth × depth):** from the first pass, take the
   recurring methods / authors / datasets you didn't know before and `curl`
   them again — at least a second layer. Stop when the timeline is *connected*
   (you can trace founding → frontier without gaps), not when you hit a count.

## Hard rules (the reviewer audits these)
- **Never write a literature entry from model memory.** Every entry traces to a
  real `curl` arxiv/Crossref hit in the execution log.
- **Never claim "queried / searched official sources" in any metadata** unless
  the log contains the matching real `curl` calls.
- The bar is **timeline coherence and completeness** (is the lineage told
  end-to-end?), NOT the number of references.

## What the reviewer will check
- `grep` the engineer execution log for real `curl` calls to
  `export.arxiv.org` / `api.crossref.org`; **zero real searches → block**.
- Spot-check ≥2 timeline nodes' URLs/DOIs by re-curling them — must really
  exist and match the claimed title/year.
- A broken/gappy timeline, an isolated paper list with no lineage, or a
  metadata claim of "queried" with no curl in the log → **block and require a
  redo with a connected, source-verified timeline.**
