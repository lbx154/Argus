---
name: Claims Evidence Audit
description: Audit a research report or paper so every claim, number, citation placeholder, and figure reference maps to local evidence or is explicitly marked unsupported.
category: research-audit
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Claims Evidence Audit

## Description
Gate paper writing and revisions on evidence integrity. This is an argus-skill-native adaptation of ARIS result-to-claim, paper-claim-audit, and citation discipline: the agent audits artifacts cold and refuses to let unsupported claims pass as results.

## When to use
- The objective mentions claim audit, evidence audit, paper readiness, citation audit, hallucination prevention, or reviewer gate.
- A paper draft, narrative report, results report, or claims table exists.
- The agent is about to declare a paper ready and needs an integrity check.

## When NOT to use
- The project has no research artifacts yet; create a plan first.
- The task is only to run tests for software correctness.
- The operator explicitly asks for brainstorming rather than verification.

## How to solve
1. Collect claim-bearing files:
   - `paper/main.tex`, `paper/sections/*.tex`, `paper/RESULTS_REPORT.md`, `research/CLAIMS_TO_TEST.md`, README claims, captions, and abstracts.

2. Collect evidence files:
   - `experiments/**/manifest.json`, result JSON/TSV/CSV, logs, verifier outputs, generated tables, plots, and analysis scripts.
   - `paper/ARTIFACT_MANIFEST.json`; if it is missing, stale, or omits a claim-bearing generated artifact, record that as a blocking audit issue.

3. Extract claims:
   - Numeric performance claims.
   - Comparative claims such as "better", "lower cost", "faster", "more robust".
   - Scope claims such as "fully autonomous", "zero-touch", "SOTA", or "EMNLP-ready".
   - Citation claims and related-work placeholders.
   - Figure/table claims: every caption with a number, every table headline, every figure takeaway, and every significance statement. A table caption without a numerical headline or a comparative caption without paired-significance evidence is an audit issue, not only a style issue.
   - Submission-readiness claims tied to format: official ACL/EMNLP template, anonymous author block, conclusion by page 8, References/Appendix starting on page 9 or later with no total-page cap after the body, Limitations/Ethical Considerations, References before Appendix, reproducibility appendix, no `[?]`, no `% UNVERIFIED`, no placeholders, every figure labeled/referenced, at least one figure/table on each of pages 4--7, and no `Overfull \hbox > 5pt`.

4. Build `paper/CLAIMS_EVIDENCE_AUDIT.tsv`:
   - claim_id
   - claim_text
   - location
   - evidence_path
   - verdict: `supported`, `weak`, `unsupported`, `contradicted`, `stale`, `citation_missing`, or `not_applicable`
   - required_fix
   - Also write `paper/CLAIMS_EVIDENCE_AUDIT.json` with the same rows as objects so the Research Submission Assurance Gate can parse the audit without re-extracting prose.

5. Enforce fixes:
   - Supported: keep, optionally tighten wording.
   - Weak: soften language and label pilot/single-run limits.
   - Unsupported: remove or turn into future work.
   - Contradicted: correct the claim and explain in revision log.
   - Stale: regenerate the derived report/manuscript from the canonical source and refresh `paper/ARTIFACT_MANIFEST.json`; do not patch only the prose.
   - Citation missing: mark `[VERIFY_CITATION]` or remove the citation-dependent claim.

6. Verify regenerated paper/report:
   - Ensure no high-risk unsupported numeric or comparative claim remains.
   - Ensure every figure/table reference points to an existing file.
   - Ensure every figure has a `\label{}` and a text reference, every table caption has a numerical headline, and every paper-facing figure/table label uses human-readable names rather than snake_case/code identifiers.
   - If the audit touches format readiness, require the same `research.md` hard preflight used by the drafting and assurance skills: no unresolved refs/citations, no `[?]`, no `% UNVERIFIED` entries unless disclosed, no placeholders, no `Overfull \hbox > 5pt`, at least one paired-significance table when applicable, and a complete reproducibility appendix.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-manifest --project-root .`; digest or TSV schema drift means the audit is blocked.
   - Ensure every new TODO is explicit rather than hidden.

7. Write `paper/CLAIMS_EVIDENCE_AUDIT.md`:
   - Executive summary.
   - Blocking issues.
   - Non-blocking warnings.
   - Exact files changed or recommended changes.

8. Update the pipeline gate:
   - Update `research/PIPELINE_STATE.json` if present.
   - If any numeric or comparative claim is `unsupported` or `contradicted`, set the assurance stage to `blocked` and point the next action to paper revision or more experiments.
   - If only citation checks are environment-limited, record the limitation explicitly rather than turning it into a success-shaped pass.

## Response shape
- Report counts by verdict.
- State whether the draft is blocked by evidence issues.
- If blocked, list the minimal next experiment or edit needed to unblock.
