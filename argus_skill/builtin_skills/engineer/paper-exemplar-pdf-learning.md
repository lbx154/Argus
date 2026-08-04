---
name: "Paper Exemplar PDF Learning"
description: "Download open-access top-conference paper PDFs, extract structural evidence, and build a thick style profile before drafting an EMNLP/ACL submission."
---

## Title
Paper Exemplar PDF Learning

## Description
Use real top-conference papers as formatting and structure references before writing a new paper. URL-only exemplars are not enough: an agent that has not inspected a real PDF often cannot infer what a reviewable EMNLP/ACL paper looks like.

## Structural-gate contract (BLOCKING at draft / review / submission)

The `exemplar_grounding` harness gate hard-blocks the round unless every artifact below exists with the listed shape. This closes the v1 failure mode where the agent "freestyled" the paper structure without studying any exemplar.

| Artifact | Required at | Floor |
|---|---|---|
| `paper/style_ref/EXEMPLAR.json` | draft+ | `exemplar_schema_version=2`, ≥2 entries, each with `title`/`url`/`venue`/`year`/`local_pdf` (file on disk)/`pdf_sha256` (non-empty)/`structural_profile` |
| `structural_profile.figure_inventory` (or `figures` / `figure_table_inventory`) in EVERY exemplar | draft+ | non-empty — record what figures/tables the exemplar uses so this paper can mirror the plan |
| **`format_facts`** (or `format_facts_path` pointing at one) in EVERY exemplar | draft+ | produced by `python -m argus_skill.verticals.research.format_facts <local_pdf> --write paper/style_ref/exemplars/<slug>/format_facts.json` — includes page structure, real layout blocks, columns, images, tables, citations, and section lengths |
| **`paper/PAPER_FORMAT_FACTS.json`** | draft+ | produced by `python -m argus_skill.verticals.research.format_facts paper/main.pdf --write paper/PAPER_FORMAT_FACTS.json` after every PDF rebuild. Differences from the primary exemplar are Reviewer evidence, not a mechanical pass/fail gate. |
| `paper/style_ref/STYLE_PROFILE.md` | draft+ | ≥2000 chars (no one-line stubs) |
| `paper/style_ref/EXEMPLAR_SUITABILITY.json` | draft+ | `verdict=="PASS"`, `primary_exemplar` matches a slug in EXEMPLAR.json, `no_prose_copy_attestation=true` |
| `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` | draft+ | ≥1500 chars |
| `paper/style_ref/STRUCTURE_CONFORMANCE.json` | submission | `conformance_schema_version=1`, `verdict=="PASS"`, `section_mappings` non-empty |

**Format-facts workflow** — run **once per exemplar at fetch time** and **after every paper PDF rebuild**:

```bash
# Once per exemplar (cached in EXEMPLAR.json sidecar)
python -m argus_skill.verticals.research.format_facts \
  paper/style_ref/exemplars/<slug>/paper.pdf \
  --write paper/style_ref/exemplars/<slug>/format_facts.json

# After every paper/main.pdf rebuild (the gate compares this against primary)
python -m argus_skill.verticals.research.format_facts paper/main.pdf \
  --write paper/PAPER_FORMAT_FACTS.json
```

Treat large differences as inspection pointers, not defects. Render the current
paper and primary exemplar side by side, then let the Reviewer judge hierarchy,
information density, whitespace, column balance, and figure/table readability.
Never adjust content merely to match an exemplar's counts, and never edit the
facts file by hand.

If a candidate exemplar does not pass suitability, fetch a better one — do not draft from a bad template. Hand-typed EXEMPLAR entries without an actual readable local PDF are rejected.

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
- `paper/style_ref/EXEMPLAR_SUITABILITY.json`: a pre-draft suitability lock showing why the primary exemplar is structurally appropriate for this project.
- `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`: a project-specific outline that maps exemplar structure to this paper before prose is written.
- After drafting, `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` must prove the final LaTeX section order still follows the blueprint. These are post-draft artifacts, not prerequisites for the exemplar-grounding and structure-blueprint requirement.
- `paper/style_ref/SOURCES.md`: source URLs, PDF URLs, access date, license/terms notes, and extraction commands.

## Exemplar selection contract
1. Include at least two excellent open-access papers to avoid copying one paper's quirks.
2. Include at least one recent EMNLP/ACL best/outstanding/award paper when available. For a current EMNLP submission, prefer the previous year's official EMNLP awards page and ACL Anthology PDF.
3. Add a same-direction exemplar for method/evaluation structure when the award paper is not topically aligned.
4. Do not use non-open PDFs, private review copies, or paywalled files.
5. If license terms do not permit redistribution, set `pdf_storage_policy: "local_research_cache_not_redistributed"` and keep the PDF as a local workspace artifact rather than a vendored public asset.
6. Before locking a primary exemplar, write `paper/style_ref/EXEMPLAR_SUITABILITY.json` with `verdict: "PASS"`, `primary_exemplar`, `no_prose_copy_attestation: true`, and candidate scores/rationales for task type, method family, experiment shape, figure/table density, related-work shape, and page rhythm. The primary exemplar slug must match one downloaded exemplar in `EXEMPLAR.json`; if no candidate passes, fetch a better exemplar instead of drafting from a bad template.

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

`paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` must turn the selected exemplar into a concrete writing scaffold for this project: section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, local evidence mapping, and a no-prose-copy policy. Start by mirroring the primary exemplar's paper skeleton and page rhythm directly, then make only evidence-justified adaptations such as renaming a section title for this project's thesis or merging/splitting a role when the local evidence truly requires it. Use this reference page budget as the default anchor, then justify any paper-specific changes: Abstract 0.3 pages; Introduction 1 page; Related Work 0.5--0.8 pages; Method 1--1.5 pages; Experimental Setup 0.5--1 page; Main Results 1--1.5 pages; Analysis/Ablation 1 page; Failure Cases 0.3--0.5 pages; Conclusion 0.2 pages. This blueprint is the pre-draft paper organizer; do not let the agent improvise body sections from memory.

After the manuscript exists, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json`. The JSON must use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every final top-level section before references/appendix. Each mapping must include `section`, `maps_to_exemplar_phase`, `evidence_sources`, `exemplar_lesson`, and a `deviation_rationale` for any paper-specific or nonstandard section. This allows the paper to vary from the exemplars when the local thesis/evidence requires it, but blocks unmapped filler sections such as protocol notes, track mechanics, and release details.

## Procedure
1. Find official sources first: ACL Anthology paper page, official EMNLP/ACL awards page, arXiv only when conference PDF metadata is unavailable.
2. Download the PDF into `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. Extract text into `paper/style_ref/exemplars/<slug>/paper.txt`.
4. Record the local PDF path, source URL, retrieval date, and license/storage policy.
5. Write or update `paper/style_ref/EXEMPLAR.json`.
6. Read the PDFs/text extracts and write `paper/style_ref/STYLE_PROFILE.md` from structural observations only.
7. Write `paper/style_ref/EXEMPLAR_SUITABILITY.json`; do not lock the primary exemplar until the candidate passes the suitability dimensions and matches a downloaded slug in `EXEMPLAR.json`.
8. Write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` by adapting the primary exemplar skeleton to this project's thesis, evidence, figures, tables, and section/page plan. Keep title/section wording flexible but not freeform: every rename, merge, or split needs a local-evidence rationale.
9. After the final body draft exists, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual `paper/main.tex` section order.
10. Run:
   - self-audit the exemplar-grounding and structure-blueprint requirement; the L2 reviewer verifies these artifacts directly against the draft stage checklist.
11. If validation fails, fix the missing PDF/text/hash/profile/suitability/blueprint evidence before paper drafting continues. Final readiness later self-audits the selected venue's full submission contract, which also checks structure conformance.

## Hard rules
- Never treat an ACL Anthology URL as enough. The PDF and text extract must exist locally.
- Never copy exemplar prose, examples, claims, terminology, figure design, bibliography text, or sentence templates.
- Never use exemplar structure to justify unsupported claims in the new paper.
- Never lock an exemplar only because it is famous; `EXEMPLAR_SUITABILITY.json` must show structural fit for this project's task, method, experiments, visual density, related work, and page rhythm.
- Never start body prose until the structure blueprint exists and maps exemplar lessons to local evidence; otherwise the draft will regress into freehand filler.
- Never leave a final top-level section unmapped in `STRUCTURE_CONFORMANCE.json`; if a project-specific section is genuinely needed, justify the deviation and point to local evidence.
- If web access is unavailable, write `paper/style_ref/TODO.md` and mark the draft blocked; do not claim EMNLP-ready status.

## Response shape
- Name the downloaded exemplar PDFs and text extracts.
- Name the primary exemplar suitability verdict and any rejected candidate reasons.
- Name the structure blueprint and the exemplar-derived section/page decisions it imposes.
- State whether the exemplar-grounding and structure-blueprint requirement holds.
- If blocked, list the exact missing artifacts or license/source issues.
