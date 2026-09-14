---
name: "Semantic Scholar Literature Search"
description: "检索论文和引用信息并核实版本、发表状态。 Search Semantic Scholar for relevant papers; distinguish indexed preprints from accepted publications, verify venue claims and preserve source uncertainty."
---

# Semantic Scholar Literature Search

Use for literature discovery or a claim that needs a paper source. Semantic Scholar
indexes multiple publication types, including preprints; being indexed or highly
cited does not prove peer review, acceptance, relevance or correctness.

## Focused search

Start with the actual question. Set a year range relative to the current date only
when recency matters. Filter fields or publication types only when the task calls
for them; do not force Computer Science on another discipline.

```python
import json
import urllib.parse
import urllib.request

params = {"query": "the question from the current task", "limit": 5,
          "fields": "title,authors,year,venue,publicationTypes,externalIds,openAccessPdf,url"}
url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
with urllib.request.urlopen(url, timeout=20) as response:
    papers = json.load(response)
```

Use the current official API contract if fields change. Honor 429/Retry-After and
bounded retries; use an existing authorized API key through the configured client
without printing it. An unavailable search API is not proof a paper does not exist.

## Verify only the sources that matter

- Match title/authors/year/identifier and distinguish versions or duplicates.
  A missing `externalIds.ArXiv` value means unknown linkage, not “venue-only”.
- Verify acceptance/venue against the official program or proceedings. An arXiv
  record, conference template, citation count or a non-empty venue field alone is
  insufficient evidence of acceptance.
- Read the relevant full-text passage before making a technical or quantitative
  claim. TLDR and abstracts are discovery aids; do not invent a missing summary.
- An exact-title miss may reflect indexing lag, especially for workshops. Check
  the official venue record, versioned arXiv source/PDF or author repository.
  Clearly distinguish workshop, main proceedings, poster and non-archival records.
- Limit claims to what accessible evidence supports; a blocked PDF may still allow
  an official abstract to support a narrower statement.

Return a short relevant set with identifiers/links and the passages needed by the
question. Include citation counts only when useful, with retrieval date and no
quality ranking inferred from counts. Record durable factual knowledge in the
existing Wiki only if it adds something new; do not generate a page per hit.
