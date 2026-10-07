---
name: "Checking claims and citations against their sources"
description: "Read each material claim in the manuscript against the raw result, script, figure source or primary text that is supposed to support it, and each claim-critical citation against the paper it names; repair the source or the prose, not a report about them. Covers searching Semantic Scholar and telling a preprint from an accepted publication."
---

# Checking claims and citations against their sources

The global library's `engineer/web-primary-source-evidence.md` says how any
current fact is grounded: classify the claim, fetch the primary source before
citing it (`python -m argus.tools.web_source <URL>` caches it under
`.argus/sources/` with an access date), and keep the URL and cached path beside
the claim rather than in a separate evidence file. This skill adds the two
judgements a paper needs on top of that: whether a sentence in the manuscript
says what its evidence supports, and whether a citation names a text that says
what the sentence attributes to it.

## When a claim may have drifted from its evidence

Use this when a draft contains quantitative, comparative, novelty, causal or
scope claims that may no longer match the current experiments or the
literature: after results were regenerated, after an implementation or
evaluation contract changed, or in the final review. Read the claim in context
in `paper/main.tex`, then open the thing that should support it: the raw result
rows, the analysis script, the figure or table source, or the primary citation.
The question is whether the wording is supported, too broad, stale,
contradicted, or missing a citation, and only reading the source answers it;
the number in the draft may have been true of an earlier run.

For a concrete high-impact ambiguity that the existing evidence and the host's
assessments do not settle, use a fresh-context check of the claim and its
direct source: a reader who has not seen the argument, asked for the exact
discrepancy, its source and a useful repair in ordinary language. Group related
assertions that share evidence instead of launching one reader per sentence;
no fixed labels are required. Keep the check narrow, do not commission another
integrated paper review, and do not hand the reader the Engineer's preferred
conclusion or permission to edit the main review report. Reuse independent
findings that already exist.

Repair the authoritative source rather than the sentence alone:

- run the decisive experiment when the evidence is genuinely missing;
- regenerate a stale number or figure;
- when an implementation or evaluation contract changed, rerun the affected
  comparison under that version rather than relabeling old panels or
  extrapolating a small repair check to the whole claim;
- raise a claim the evidence already supports more strongly than the text
  says: an understated result is as much a mismatch as an overstated one;
- expose an adverse comparison or an uncertainty the text hides;
- add a verified primary citation;
- strengthen the method or run a feasible decisive test before defaulting to
  weaker prose; when the evidence disproves a claim, keep that result and
  revise the interpretation instead of testing repeatedly for the wanted
  answer.

Then recompile and reread the affected paragraph, table or caption as a
reviewer would. Check every material claim, but do not build a claim map,
inspection table, gap list or parallel report to record that the check
happened; existing files of that kind are historical notes, not completion
conditions. Summarize what changed and any unresolved scientific gap in
ordinary prose, and leave raw data and analysis outputs intact.

## When a sentence depends on a citation

For each disputed or claim-critical citation, read the surrounding sentence and
the bibliography entry, resolve the paper through a primary source (publisher,
DOI, official proceedings, arXiv, OpenReview, ACL Anthology), and verify title,
authors, year, venue and, above all, that the source says what the sentence
attributes to it. Repair the bibliography or the prose directly; a citation
that cannot be resolved is removed or replaced with a real source that supports
the claim, and the paper is recompiled afterwards. Bibliographic facts are
copied from the source, never recalled: model memory conflates versions, venues
and authors precisely for the papers cited most. There is no citation-count
target and no per-citation record. An unresolved material citation is reported
through the scientific review so that `paper/REVIEW.md` says the paper does not
hold until it is resolved.

## Finding and verifying papers with Semantic Scholar

Semantic Scholar indexes preprints alongside publications; being indexed or
highly cited proves neither peer review nor relevance nor correctness. Start
from the actual question, set a year range only when recency matters, and
filter fields or publication types only when the task calls for it (do not
force Computer Science onto another discipline).

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

Follow the current official API contract if fields change, honour 429 and
`Retry-After` with bounded retries, and use an existing authorized API key
through the configured client without printing it. An unavailable search API
is not evidence that a paper does not exist.

Verify only the sources that matter, and verify them against what they are.
Match title, authors, year and identifier and tell versions and duplicates
apart; a missing `externalIds.ArXiv` means unknown linkage, not "venue-only".
Confirm acceptance against the official program or proceedings, since an arXiv
record, a conference template, a citation count or a non-empty venue field
alone does not establish it. Read the relevant full-text passage before making
a technical or quantitative claim; TLDRs and abstracts are discovery aids, and
a missing summary is not invented. An exact-title miss may be indexing lag,
especially for workshops, so check the venue record, the versioned arXiv source
or the author repository, and say clearly whether a record is a workshop
paper, main proceedings, a poster or non-archival. Limit each claim to what the
accessible evidence supports; a blocked PDF may still leave an official
abstract able to carry a narrower statement.

Return the short relevant set with identifiers, links and the passages the
question needs. Include citation counts only when they are useful, with the
retrieval date, and infer no quality ranking from them. Record durable factual
knowledge in the existing Wiki only when it adds something new; do not
generate a page per hit.
