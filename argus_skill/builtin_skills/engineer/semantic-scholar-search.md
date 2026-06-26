---
name: semantic-scholar-search
description: "Search published venue papers (IEEE, ACM, Springer, etc.) via Semantic Scholar API. Provides citation counts, venue metadata, and TLDR. Complements arXiv (preprints) with published literature. Use for literature search, citation finding, or venue-specific paper discovery."
category: literature
version: "1.0"
created_at: "2025-07-27"
---

# Semantic Scholar Paper Search

Search published venue papers with citation metadata.

## Role & Positioning

| Source | Best for |
|--------|----------|
| arXiv | Latest preprints, cutting-edge unrefereed work |
| Semantic Scholar | **Published** journal/conference papers with citation counts, venue info, TLDR |

## API Usage

Base URL: `https://api.semanticscholar.org/graph/v1/paper/search`

### Search Query

```python
import urllib.request, urllib.parse, json

def search_s2(query, max_results=10, year=None, fields_of_study=None):
    """Search Semantic Scholar for published papers."""
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,authors,year,venue,citationCount,abstract,externalIds,openAccessPdf,tldr,publicationTypes,fieldsOfStudy"
    }
    if year:
        params["year"] = year  # e.g., "2022-" or "2020-2024"
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study  # e.g., "Computer Science"
    
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    # Optional: add API key for higher rate limits
    # req.add_header("x-api-key", os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""))
    
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
```

### Fetch Single Paper

```python
def fetch_paper(paper_id):
    """Fetch details for a specific paper by ID (DOI, ArXiv ID, S2 ID)."""
    fields = "title,authors,year,venue,citationCount,abstract,externalIds,openAccessPdf,tldr,references,citations"
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields={fields}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
```

### Paper ID Formats

- DOI: `10.1109/TWC.2024.1234567`
- ArXiv: `ARXIV:2006.10685`
- Corpus: `CorpusId:219792180`
- S2 ID: 40-char hex string

## Workflow

### Step 1: Parse Query

Determine search parameters:
- Main query terms (technical keywords)
- Year filter (default: last 3 years for recent work)
- Field filter (default: "Computer Science" for ML/AI work)
- Publication type (JournalArticle, Conference, or both)

### Step 2: Execute Search

Apply recommended defaults:
- `fields_of_study = "Computer Science"` (reduces cross-discipline noise)
- `year = "2022-"` (focus on recent work unless user specifies otherwise)
- `max_results = 10`

### Step 3: De-duplicate Against arXiv

For each result, check `externalIds.ArXiv`:
- If present → paper is also on arXiv (note but don't re-fetch)
- If absent → paper is venue-only (unique value of this search)

### Step 4: Present Results

```markdown
| # | Title | Venue | Year | Citations | Authors |
|---|-------|-------|------|-----------|---------|
| 1 | ... | IEEE Trans. SP | 2023 | 142 | Smith et al. |

For each paper:
- **DOI**: https://doi.org/...
- **TLDR**: [one-line summary if available]
- **Open Access**: [PDF link if available]
- **Also on arXiv**: [ID if exists]
```

### Step 5: Detailed Summary (top 5)

For each of the top results:
```markdown
## [Title]
- **Venue**: [venue] | **Year**: [year] | **Citations**: [count]
- **Authors**: [list]
- **Abstract**: [text]
- **Key contribution**: [1-2 sentences]
```

## Key Rules

- **Always filter by field**: Without `fieldsOfStudy`, S2 returns cross-discipline noise
- **Citation count is gold**: S2's citation data is its main advantage — always show prominently
- **Venue metadata matters**: Show venue type (journal vs conference) for quality assessment
- **Rate limiting**: Without API key, limited to ~1 req/s. Set `SEMANTIC_SCHOLAR_API_KEY` for higher limits
- **TLDR may be null**: Some publishers elide it — fall back to first sentence of abstract
- **DOI is canonical**: Always provide DOI links for published papers

## Integration

- Called by `auto-research-pipeline` during literature stage
- Feeds into `novelty-check` for prior work identification
- Results referenced by `emnlp-paper-drafting` for related work section
- Complements web search for comprehensive literature coverage

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
        ingested_by=f"semantic-scholar-search@mission-{mission_id}",
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
