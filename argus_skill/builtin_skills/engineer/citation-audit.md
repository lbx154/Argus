---
name: Citation Audit
description: Zero-context bibliographic verification — for every \cite{...} in the paper, an isolated reviewer agent checks (1) the cited paper exists (arXiv/DOI/venue), (2) author/year/venue/title match canonical sources (DBLP, arXiv, ACL Anthology, OpenReview), and (3) the citation context matches what the cited paper actually claims. Catches hallucinated authors, fabricated venues, wrong years, version mismatches, and wrong-context citations.
category: paper-review
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-01T00:00:00+00:00
---

# Citation Audit (zero-context, cross-model)

> Adapted from ARIS `citation-audit` skill (MIT, © 2026 wanshuiyin).
> Replaces ARIS's external Codex MCP path with argus's reviewer/scientist
> backend; runs each per-entry check against the same model API vault
> that powers the rest of the pipeline.

## When to invoke

Run before `submission` stage, after the bibliography is frozen. The
audit is verdict-bearing; do not re-run on a wall-clock timer (the
verdict only changes when the bibliography or section context changes).

## What this catches

| Failure mode | How |
|---|---|
| **Hallucinated paper** | `arXiv:2401.99999` doesn't resolve / no matching DOI |
| **Wrong author** | `Smith et al. 2024` cites a paper actually by Chen et al. |
| **Wrong year** | cite says 2024, paper appeared 2022 |
| **Fabricated venue** | "ICML 2024" entry that's actually a workshop or never published |
| **Version mismatch** | citation references v3 features, but only v1 exists |
| **Wrong-context citation** | `\cite{x}` placed to support claim C, but paper x establishes ¬C |

## Workflow

The reviewer agent (gpt-5.5 from `scientist`/`reviewer` route) is
invoked **once per bib entry, with zero shared context** so an earlier
correct citation cannot create confirmation bias for the next.

### Step 1 — collect every (cite_key, surrounding_context) pair

```bash
grep -rEn '\\cite[a-z]*\{([^}]+)\}' paper/*.tex paper/sec/*.tex paper/sections/*.tex 2>/dev/null
```

For each cite_key:
- Pull the full sentence containing the `\cite{...}` (the **context**).
- Look up the bib entry in `paper/refs.bib` (the **claimed metadata**).

### Step 2 — per-entry audit prompt

For every `(cite_key, context, bib_entry)` triple, dispatch a fresh
reviewer call (no prior thread). The prompt:

```
You are a bibliographic auditor with zero prior context. Verify ONE citation.

cite_key: {{cite_key}}

claimed bib entry:
{{bib_entry verbatim}}

context where this citation appears:
{{surrounding sentence verbatim}}

Tasks:
1. EXISTENCE — does the cited paper actually exist?
   - If an arXiv ID is given, resolve it (WebFetch arxiv.org/abs/<id>).
   - If a DOI is given, resolve it (doi.org/<doi>).
   - If only venue+year+title, search ACL Anthology / DBLP / OpenReview / Google Scholar.
2. METADATA — do the bib entry's author, year, venue, and title MATCH the canonical source?
3. CONTEXT — does the cited paper actually establish what the sentence says it does?

Reply in JSON:
{
  "cite_key": "...",
  "verdict": "ok" | "soft" | "hard",
  "issues": [{"field": "author"|"year"|"venue"|"title"|"existence"|"context", "actual": "...", "claimed": "..."}],
  "fix": "remove" | "replace_metadata" | "replace_context" | "keep",
  "evidence": "<brief, citing canonical source URL>"
}
```

`verdict=hard` means do not submit with this citation. `verdict=soft`
means metadata polish (e.g. capitalized title vs all-lowercase) but
not blocking. `verdict=ok` is no action.

### Step 3 — aggregate

Build `paper/CITATION_AUDIT.md` grouping by verdict. Required sections:
- `## Hard failures (must fix before submission)` — listed with cite_key + fix recommendation
- `## Soft warnings (recommended polish)`
- `## Verified clean (no action)`

### Step 4 — apply fixes (interactive)

For each `hard` entry, apply the recommended fix:
- `remove` → delete `\cite{...}` and the corresponding bib entry
- `replace_metadata` → update `paper/refs.bib` with the canonical values
- `replace_context` → either move the `\cite{...}` to a sentence the cited paper actually supports, OR replace with a real citation that does support the original claim

After each batch of fixes, re-compile and re-run the audit on the
changed entries only.

## Anti-patterns

- ❌ Sending all bib entries to one reviewer call — that lets correct
  earlier entries bias the verdict on suspicious later ones. Always
  one-call-per-entry.
- ❌ Using the reviewer's prior thread (`codex-reply`) for the next
  entry — same reason; cross-context contaminates.
- ❌ Treating reviewer "looks reasonable" as `ok` without an evidence
  URL. Every `ok` must cite a canonical source URL the reviewer
  actually fetched.

## Output contract

Writes:
- `paper/CITATION_AUDIT.md` — human-readable report
- `paper/CITATION_AUDIT.json` — machine-readable verdicts for the
  `evidence_chain` / reviewer integration

Exit status: this skill emits a report and lets the reviewer rule on
"is the paper submission-ready". The harness does not gate on the
citation count — that's a research-quality call.

## Integration with argus

The reviewer reading `paper/CITATION_AUDIT.md` at the `review` /
`submission` stages can fold these findings into its checklist alongside
the F4 evidence_chain output. Argus does not block on citation soft
warnings — the reviewer decides what's submission-blocking.
