---
name: Paper Exemplar PDF Learning
description: Download open-access top-conference paper PDFs, extract structural evidence, and build a thick style profile before drafting an EMNLP/ACL submission.
category: paper-audit
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-25T00:00:00+00:00
---

## Title
Paper Exemplar PDF Learning

## Description
Use real top-conference papers as formatting and structure references before writing a new paper. URL-only exemplars are not enough: an agent that has not inspected a real PDF often cannot infer what a reviewable EMNLP/ACL paper looks like.

## When to use
- Before EMNLP/ACL paper drafting begins.
- When `paper/style_ref/EXEMPLAR.json` exists but only contains URLs or metadata.
- When a draft has weak paper structure, ugly layout, generic academic prose, or no clear sense of top-conference density.

## Required output artifacts
- `paper/style_ref/EXEMPLAR.json` with `exemplar_schema_version: 2`.
- At least two exemplar directories under `paper/style_ref/exemplars/<slug>/`.
- For each exemplar:
  - `paper.pdf`: the downloaded open-access PDF or documented local research cache copy.
  - `paper.txt`: extracted UTF-8 text from the PDF using `pdftotext`, `pypdf`, `pdfminer.six`, or another reliable extractor.
  - JSON metadata in `EXEMPLAR.json`: `title`, `url`, `venue`, `year`, `source_type`, `award_status` when applicable, `open_access: true`, `license`, `pdf_storage_policy`, `usage: "structural_style_only"`, `no_prose_copy: true`, `local_pdf`, `pdf_sha256`, `text_extract`, and `structural_profile`.
- `paper/style_ref/STYLE_PROFILE.md`: a thick structural profile, not a one-line note.
- `paper/style_ref/SOURCES.md`: source URLs, PDF URLs, access date, license/terms notes, and extraction commands.

## Exemplar selection contract
1. Include at least two excellent open-access papers to avoid copying one paper's quirks.
2. Include at least one recent EMNLP/ACL best/outstanding/award paper when available. For a current EMNLP submission, prefer the previous year's official EMNLP awards page and ACL Anthology PDF.
3. Add a same-direction exemplar for method/evaluation structure when the award paper is not topically aligned.
4. Do not use non-open PDFs, private review copies, or paywalled files.
5. If license terms do not permit redistribution, set `pdf_storage_policy: "local_research_cache_not_redistributed"` and keep the PDF as a local workspace artifact rather than a vendored public asset.

Accepted `license` values for the validator:
- `cc-by-4.0`
- `cc-by-sa-4.0`
- `cc-by-nc-4.0`
- `acl-anthology-open-access`
- `arxiv-nonexclusive-local-cache`
- `publisher-open-access-local-cache`

Accepted `pdf_storage_policy` values:
- `redistributable_open_access`
- `local_research_cache_not_redistributed`

## Thick profile requirements
`paper/style_ref/STYLE_PROFILE.md` must be long enough to teach the agent the distribution of strong papers. Include these sections:

1. **Abstract shape**: sentence roles and evidence placement.
2. **Section/page allocation**: approximate page budget and section order.
3. **Figure/table inventory**: body and appendix visual density, caption style, and table placement.
4. **Related-work shape**: how the exemplar organizes prior work by gap rather than chronology.
5. **Evaluation layout**: setup, baselines, main result, ablation, robustness/transfer, and failure analysis ordering.
6. **Formatting/layout lessons**: float density, table readability, page-wall avoidance, references-before-appendix ordering, and visual polish.
7. **Writing lessons**: concrete verbs, calibrated claims, contribution framing, and avoidance of generic openings.
8. **Transfer plan**: how those structural lessons will change this paper.
9. **No prose copy policy**: explicit statement that the exemplar is for structure only.

## Procedure
1. Find official sources first: ACL Anthology paper page, official EMNLP/ACL awards page, arXiv only when conference PDF metadata is unavailable.
2. Download the PDF into `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. Extract text into `paper/style_ref/exemplars/<slug>/paper.txt`.
4. Compute `sha256sum paper/style_ref/exemplars/<slug>/paper.pdf` and record it as `pdf_sha256`.
5. Write or update `paper/style_ref/EXEMPLAR.json`.
6. Read the PDFs/text extracts and write `paper/style_ref/STYLE_PROFILE.md` from structural observations only.
7. Run:
   - `python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`
8. If validation fails, fix the missing PDF/text/hash/profile evidence before paper drafting continues.

## Hard rules
- Never treat an ACL Anthology URL as enough. The PDF and text extract must exist locally.
- Never copy exemplar prose, examples, claims, terminology, figure design, bibliography text, or sentence templates.
- Never use exemplar structure to justify unsupported claims in the new paper.
- If web access is unavailable, write `paper/style_ref/TODO.md` and mark the draft blocked; do not claim EMNLP-ready status.

## Response shape
- Name the downloaded exemplar PDFs and text extracts.
- State whether `validate-exemplar` passed.
- If blocked, list the exact missing artifacts or license/source issues.
