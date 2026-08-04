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
3. Decide whether the wording is supported, too broad, stale, contradicted, or
   missing a citation.
4. Repair the authoritative source:
   - regenerate a stale number or figure;
   - narrow or remove an unsupported claim;
   - expose an adverse comparison or uncertainty;
   - add a verified primary citation;
   - request the smallest decisive experiment when evidence is genuinely missing.
5. Recompile and reread the affected paragraph, table, or caption as a paper
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
