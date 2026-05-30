---
name: arxiv-paper-search
description: "Search arXiv preprints via the public API. Covers latest cutting-edge work before formal publication. Complements Semantic Scholar (published venue papers) with preprint coverage. Use for literature search, related work discovery, or tracking SOTA."
category: literature
version: "1.0"
scientist_model: gpt-5.5
created_at: "2026-05-28"
---

# arXiv Paper Search

Search arXiv preprints via the official API for latest cutting-edge work.

## Role & Positioning

| Source | Best for |
|--------|----------|
| **arXiv (this skill)** | Latest preprints, cutting-edge unrefereed work, CS/ML/AI papers |
| Semantic Scholar | Published journal/conference papers with citation counts, venue info |

Use both together in the **research** stage: arXiv for recent/SOTA, Semantic Scholar for established/cited work.

## API Usage

Base URL: `http://export.arxiv.org/api/query`

### Search

```python
import urllib.request, urllib.parse, xml.etree.ElementTree as ET

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}

def search_arxiv(query, max_results=10, sort_by="relevance", start=0, category=None):
    """Search arXiv for preprints.
    
    Args:
        query: search terms (e.g., "web agent benchmark")
        max_results: number of results (max 100)
        sort_by: "relevance" or "lastUpdatedDate" or "submittedDate"
        start: pagination offset
        category: arXiv category filter (e.g., "cs.CL", "cs.AI", "cs.LG")
    """
    search_query = f"all:{query}"
    if category:
        search_query = f"cat:{category} AND all:{query}"
    
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    url = f"http://export.arxiv.org/api/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "argus-skill/1.0")
    
    with urllib.request.urlopen(req, timeout=30) as resp:
        tree = ET.parse(resp)
    
    root = tree.getroot()
    results = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title = entry.find("atom:title", ARXIV_NS).text.strip().replace("\n", " ")
        summary = entry.find("atom:summary", ARXIV_NS).text.strip().replace("\n", " ")
        arxiv_id = entry.find("atom:id", ARXIV_NS).text.strip().split("/abs/")[-1]
        published = entry.find("atom:published", ARXIV_NS).text[:10]
        authors = [a.find("atom:name", ARXIV_NS).text
                    for a in entry.findall("atom:author", ARXIV_NS)]
        categories = [c.get("term")
                      for c in entry.findall("atom:category", ARXIV_NS)]
        pdf_link = f"https://arxiv.org/pdf/{arxiv_id}"
        
        results.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": authors[:5],  # first 5 authors
            "published": published,
            "categories": categories,
            "summary": summary[:500],
            "pdf_url": pdf_link,
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return results
```

### Fetch Single Paper by ID

```python
def fetch_arxiv_paper(arxiv_id):
    """Fetch a specific arXiv paper by ID (e.g., '2306.06070')."""
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "argus-skill/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        tree = ET.parse(resp)
    root = tree.getroot()
    entry = root.find("atom:entry", ARXIV_NS)
    if entry is None:
        return None
    return {
        "arxiv_id": arxiv_id,
        "title": entry.find("atom:title", ARXIV_NS).text.strip().replace("\n", " "),
        "authors": [a.find("atom:name", ARXIV_NS).text
                     for a in entry.findall("atom:author", ARXIV_NS)],
        "summary": entry.find("atom:summary", ARXIV_NS).text.strip(),
        "published": entry.find("atom:published", ARXIV_NS).text[:10],
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
    }
```

## Common arXiv Categories for ML/NLP/Agent Research

| Category | Area |
|----------|------|
| `cs.CL` | Computational Linguistics / NLP |
| `cs.AI` | Artificial Intelligence |
| `cs.LG` | Machine Learning |
| `cs.CV` | Computer Vision |
| `cs.IR` | Information Retrieval |
| `cs.MA` | Multi-Agent Systems |
| `cs.SE` | Software Engineering |

## Workflow

### Step 1: Determine Search Strategy

For a literature survey, run multiple queries:
1. **Core topic** — the main research area (e.g., "web agent grounding")
2. **Method keywords** — specific techniques (e.g., "DOM tree element scoring")
3. **Benchmark names** — specific datasets/benchmarks (e.g., "Mind2Web" OR "WebArena")
4. **Recent SOTA** — sort by `submittedDate` with category filter

### Step 2: Execute Searches

```python
# Example: survey for a web-agent paper
core = search_arxiv("web agent grounding benchmark", max_results=15, category="cs.CL")
methods = search_arxiv("DOM element scoring neural", max_results=10, category="cs.AI")
benchmarks = search_arxiv("Mind2Web OR WebArena OR MiniWoB", max_results=10,
                          sort_by="submittedDate")
```

### Step 3: Record in LITERATURE_GROUNDING.json

For each relevant paper found, add an entry:
```json
{
  "arxiv_id": "2306.06070",
  "title": "Mind2Web: Towards a Generalist Agent for the Web",
  "authors": ["Xiang Deng", "..."],
  "year": 2023,
  "venue": "NeurIPS 2023",
  "source": "arxiv",
  "relevance": "Primary benchmark for web agent evaluation",
  "bibtex_key": "deng2023mind2web"
}
```

### Step 4: Generate BibTeX

```bibtex
@article{deng2023mind2web,
  title={Mind2Web: Towards a Generalist Agent for the Web},
  author={Deng, Xiang and Gu, Yu and Zheng, Boyuan and ...},
  journal={arXiv preprint arXiv:2306.06070},
  year={2023}
}
```

## Rate Limits

- arXiv API: ~3 requests/second, no API key needed
- Add 1-second delay between batch queries
- For bulk search (>100 papers), paginate with `start` parameter

## Integration

- Called during **research** stage alongside `semantic-scholar-search`
- Feeds into `research/LITERATURE_GROUNDING.json` and `paper/refs.bib`
- Used by `research-brief-to-experiment-plan` for literature-grounded planning
