---
name: Paper Claim Audit
description: Zero-context paper-to-evidence fidelity audit — for every number, comparison, and scope claim in the paper draft, a fresh reviewer agent with no prior thread checks whether the raw result files actually support it. Catches inflated scores, swapped-condition tables, baselines reported as proposed, "improves over X" with no matching evidence row, range exaggeration (e.g. "5–10%" when only 4% measured).
category: paper-review
version: 1
author_model: gpt-5.5
created_at: 2026-06-01T00:00:00+00:00
---

# Paper Claim Audit (zero-context, cross-model)

> Adapted from ARIS `paper-claim-audit` skill (MIT, © 2026 wanshuiyin).
> Complements argus's F4 `evidence_chain` (which checks structural
> existence of bundles) by checking that paper-stated numbers actually
> match what the underlying evidence files contain.

## Why this exists

`evidence_chain` (F4) verifies: *claim → cited file exists → bundle
has BUILD_INFO*. It does NOT verify: *the number in the paper matches
the number in the file*. A paper can pass F4 and still say
`reward = 0.82` when `summary.tsv` shows `0.66` — F4 only checks
that summary.tsv exists, not its contents vs. the claim.

This skill closes that gap with a fresh-context cross-model reviewer.

## How this differs from other audit skills

| Skill | Checks |
|---|---|
| F4 `evidence_chain` | structural: every cited path exists; every bundle has BUILD_INFO; tainted bundles aren't cited as current evidence |
| `claims-evidence-audit` (argus existing) | claim ↔ evidence pairing in `claims_to_evidence.tsv` |
| **paper-claim-audit (this)** | **every number/comparison/scope claim in `paper/main.tex` matches the actual content of the cited evidence file** |
| `citation-audit` | bibliography correctness, not numbers |

## Core principle

The reviewer must be **fresh-context** — no prior conversation about
this paper, no access to the engineer's notes, no access to the
paper's narrative. ONLY:
- the bare claim sentence from `main.tex`
- the raw evidence file the paper cites for that claim

This prevents confirmation bias ("the engineer says it's 0.82 and the
table sort-of says 0.82").

## Workflow

### Step 1 — collect claim/evidence pairs

For each numeric or comparison claim in `paper/main.tex`:

```bash
# Numbers
grep -nE '[0-9]+\.[0-9]+%?|\\textbf\{[0-9]' paper/main.tex paper/sec/*.tex
# Comparison verbs
grep -nE 'improves over|outperforms|reduces|increases|achieves' paper/main.tex paper/sec/*.tex
# Range claims
grep -nE '[0-9]+(\.[0-9]+)?\s*[-–—]\s*[0-9]+' paper/main.tex paper/sec/*.tex
```

For each match, find the nearest `\cite{...}` or table/figure
reference. The pair `(claim_sentence, evidence_path)` becomes one
audit task. If no evidence is cited within ±3 sentences, that itself
is an issue ("unsourced claim").

### Step 2 — fresh-reviewer audit (gpt-5.5, NEW thread per claim)

For each `(claim_sentence, evidence_path)`:

```
You are auditing one paper claim with ZERO prior context.

Paper sentence (verbatim, no surrounding paragraph):
{{claim_sentence}}

Cited evidence file (verbatim contents):
{{open evidence_path and paste relevant rows}}

Tasks:
1. EXTRACT every quantitative or comparative assertion in the sentence.
   (e.g. "achieves 0.82 reward", "improves over Baseline by 12%",
   "across 3 benchmarks")
2. For each assertion, find the supporting value in the evidence file.
3. Report MATCH / MISMATCH / MISSING per assertion.

Reply in JSON:
{
  "claim_sentence": "...",
  "evidence_path": "...",
  "assertions": [
    {"text": "achieves 0.82 reward",
     "evidence_value": "0.66",
     "verdict": "MISMATCH",
     "delta": "0.16 absolute"},
    ...
  ],
  "overall_verdict": "PASS" | "WARN" | "FAIL"
}
```

The reviewer is launched **once per claim** with no shared thread —
this is the architecturally critical bit, the same principle as
citation-audit.

### Step 3 — aggregate report

Build `paper/PAPER_CLAIM_AUDIT.md` with sections:
- `## Overall verdict: PASS | WARN | FAIL`
- `## Claims verified: N total`
- `## FAIL claims (must fix before submission)` — each with sentence + evidence + delta
- `## WARN claims (rounding, scope mismatch)` — same shape
- `## PASS claims (cite_key + evidence path)` — one line each

### Step 4 — interactive fix loop

For each FAIL:
1. Show the engineer the sentence + the actual evidence value.
2. The engineer decides: edit the sentence to match the evidence,
   OR replace the evidence pointer with a different file that
   genuinely supports the claim.
3. Re-run audit on just the fixed claims.

## Output contract

- `paper/PAPER_CLAIM_AUDIT.md` — human-readable
- `paper/PAPER_CLAIM_AUDIT.json` — machine-readable
- The argus reviewer at `submission` stage reads both as additional
  evidence; the harness never gates on the verdict (which would be a
  research-quality call). The reviewer rules.

## Anti-patterns

- ❌ Letting the auditor see the surrounding paragraph — that brings
  in narrative context that biases interpretation
- ❌ Re-using one reviewer thread for all claims — confirmation bias
- ❌ Treating "approximately matches" as PASS — every assertion gets
  MATCH or MISMATCH with a delta. The reviewer decides if the delta is
  acceptable, not the harness
- ❌ Auditing only the abstract — abstract numbers are easy; the
  hidden inflation usually lives in the experimental tables and the
  conclusion's recap

## Integration with argus

The reviewer at `analysis` / `review` / `submission` stages reads
`paper/PAPER_CLAIM_AUDIT.md`. This skill is the *content* check
counterpart to F4 evidence_chain's *structure* check. Both findings
feed the reviewer's prompt; reviewer rules on what's submission-ready.
