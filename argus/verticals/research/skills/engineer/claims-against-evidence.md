---
name: "Reading claims against the evidence"
description: "Check material paper claims against their real sources and repair unsupported or overstated prose without creating a separate set of inspection records."
---

# Reading claims against the evidence

## When to use

Use this when a draft contains quantitative, comparative, novelty, causal, or
scope claims that may have drifted from the current experiments or literature.

## How to work

1. Read the claim in context in `paper/main.tex`.
2. Open the raw result, analysis script, figure/table source, or primary citation
   that should support it.
3. For a concrete high-impact ambiguity that the existing evidence and host
   assessments do not settle, use a **fresh-context** check of the claim and its
   direct source. Group related assertions that share evidence instead of
   launching one reader per sentence. Ask for the exact discrepancy, its source,
   and a useful repair in ordinary language; no fixed labels are required.
   Keep this check narrow, do not commission another integrated paper review,
   and do not give the reader the Engineer's preferred conclusion or permission
   to edit the main review report. Reuse current independent findings.
4. Decide whether the wording is supported, too broad, stale, contradicted, or
   missing a citation.
5. Repair the authoritative source:
   - run the decisive experiment when the evidence is genuinely missing;
   - regenerate a stale number or figure;
   - when an implementation or evaluation contract changed, rerun the affected
     comparison scope under that version, rather than relabeling old panels or
     extrapolating a small repair check to the whole claim;
   - raise a claim the evidence already supports more strongly than the text
     says — an under-stated result is as much a mismatch as an over-stated one;
   - expose an adverse comparison or uncertainty;
   - add a verified primary citation;
   - strengthen the method or run a feasible decisive test before defaulting to
     weaker prose; if the evidence disproves a claim, retain that result and
     revise the interpretation instead of testing repeatedly for a desired answer.
6. Recompile and reread the affected paragraph, table, or caption as a paper
   reviewer would.

Check every material claim, but do not create a claim map, inspection table, graph,
gap list, or parallel report merely to record that the check happened. Existing
files may be read as historical notes; they are not completion conditions.

## Explaining what changed

Summarize the claims changed and any unresolved scientific gap in ordinary prose.
Keep raw data and analysis outputs intact for later inspection.
