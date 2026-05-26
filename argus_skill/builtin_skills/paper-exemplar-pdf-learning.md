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
- `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`: a project-specific outline that maps exemplar structure to this paper before prose is written.
- After drafting, `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` must prove the final LaTeX section order still follows the blueprint. These are post-draft artifacts, not prerequisites for `validate-exemplar`.
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

`paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` must turn the profile into a concrete writing scaffold for this project: section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, local evidence mapping, and a no-prose-copy policy. This blueprint is the pre-draft paper organizer; do not let the agent improvise body sections from memory.

After the manuscript exists, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json`. The JSON must use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every final top-level section before references/appendix. Each mapping must include `section`, `maps_to_exemplar_phase`, `evidence_sources`, `exemplar_lesson`, and a `deviation_rationale` for any paper-specific or nonstandard section. This allows the paper to vary from the exemplars when the local thesis/evidence requires it, but blocks unmapped filler sections such as protocol notes, track mechanics, and release details.

## Procedure
1. Find official sources first: ACL Anthology paper page, official EMNLP/ACL awards page, arXiv only when conference PDF metadata is unavailable.
2. Download the PDF into `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. Extract text into `paper/style_ref/exemplars/<slug>/paper.txt`.
4. Compute `sha256sum paper/style_ref/exemplars/<slug>/paper.pdf` and record it as `pdf_sha256`.
5. Write or update `paper/style_ref/EXEMPLAR.json`.
6. Read the PDFs/text extracts and write `paper/style_ref/STYLE_PROFILE.md` from structural observations only.
7. Write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` by adapting those structural observations to this project's thesis, evidence, figures, tables, and section/page plan.
8. After the final body draft exists, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual `paper/main.tex` section order.
9. Run:
   - `python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`
10. If validation fails, fix the missing PDF/text/hash/profile/blueprint evidence before paper drafting continues. Final readiness later runs `validate-full-emnlp`, which also checks structure conformance.

## Hard rules
- Never treat an ACL Anthology URL as enough. The PDF and text extract must exist locally.
- Never copy exemplar prose, examples, claims, terminology, figure design, bibliography text, or sentence templates.
- Never use exemplar structure to justify unsupported claims in the new paper.
- Never start body prose until the structure blueprint exists and maps exemplar lessons to local evidence; otherwise the draft will regress into freehand filler.
- Never leave a final top-level section unmapped in `STRUCTURE_CONFORMANCE.json`; if a project-specific section is genuinely needed, justify the deviation and point to local evidence.
- If web access is unavailable, write `paper/style_ref/TODO.md` and mark the draft blocked; do not claim EMNLP-ready status.

## Response shape
- Name the downloaded exemplar PDFs and text extracts.
- Name the structure blueprint and the exemplar-derived section/page decisions it imposes.
- State whether `validate-exemplar` passed.
- If blocked, list the exact missing artifacts or license/source issues.
