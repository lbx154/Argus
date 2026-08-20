---
name: "Claim Check"
description: "Check material paper claims against their real sources and repair unsupported or overstated prose without creating a parallel audit bundle."
---

# Claim Check

## When to use

Use this when a draft contains quantitative, comparative, novelty, causal, or
scope claims that may have drifted from the current experiments or literature.

## How to work

1. Read the claim in context in `paper/main.tex`.
2. Open the raw result, analysis script, figure/table source, or primary citation
   that should support it.
3. For high-risk numeric, comparative, causal, or scope claims, use a
   **fresh-context** check: give an independent reviewer only the claim sentence
   and the relevant raw source excerpt. Use one reviewer thread per claim and
   require a `MATCH / MISMATCH / MISSING` verdict for each assertion. Do not give
   it the engineer's narrative or prior conclusion.
4. Decide whether the wording is supported, too broad, stale, contradicted, or
   missing a citation.
5. Repair the authoritative source:
   - run the decisive experiment when the evidence is genuinely missing;
   - regenerate a stale number or figure;
   - raise a claim the evidence already supports more strongly than the text
     says — an under-stated result is as much a mismatch as an over-stated one;
   - expose an adverse comparison or uncertainty;
   - add a verified primary citation;
   - narrow or remove a claim only when no affordable experiment would support
     it.
6. Recompile and reread the affected paragraph, table, or caption as a paper
   reviewer would.

Keep `paper/claims_to_evidence.tsv` as the one compact internal map from material
claims to real raw paths; it protects against dangling or tainted evidence. Check
every material claim, but do not create
`CLAIMS_EVIDENCE_AUDIT.tsv`, `.json`, `.md`, `CLAIM_GRAPH.json`, or
`EVIDENCE_GAPS.json` merely to record that the check happened. Existing files may
be read as historical notes; they are not completion conditions.

## Handoff

Summarize the claims changed and any unresolved scientific gap in ordinary prose.
Keep raw data and analysis outputs intact for later inspection.
