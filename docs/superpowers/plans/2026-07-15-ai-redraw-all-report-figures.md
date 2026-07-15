# AI Redraw of All Argus Report Figures — Implementation Plan

**Goal:** Replace all eight visible report/README figures with newly generated,
fully AI-rendered Blue–Gold Precision Atlas PNGs, validated by OCR, two vision
reviews, hashes, and evidence contracts.

**Architecture:** A validator defines exact per-figure content contracts and
collects Tesseract/vision evidence. Eight complete standalone prompts drive
`gpt-image-2`; concept figures and data figures are generated in separate gated
batches. After all eight pass, deterministic drawing code/assets are deleted,
LaTeX is switched to PNG, and one image-2 manifest becomes the sole provenance
source.

**Tech Stack:** `argus_skill.tools.image_tool`, `gpt-image-2`, vision review
model, Tesseract OCR, Python 3.11, pytest, LaTeX, SHA-256.

## Global Constraints

- Approved spec:
  `docs/superpowers/specs/2026-07-15-ai-redraw-all-report-figures-design.md`.
- Work only in the isolated
  `/home/argustest/.copilot/session-state/7ab35eb1-e9e4-411f-98f5-9ddb9b5fd70b/pro-readme-worktree`.
- All eight visible figures must be newly generated AI rasters.
- Generator must be `gpt-image-2`; output must be 1536×1024 PNG.
- No manual pixel edits, deterministic text/chart overlay, compositing, SVG,
  TikZ, Matplotlib, or vector replacement.
- Wrong text, number, role, arrow, or relationship means reject and regenerate.
- Data figures require exact OCR plus two independent vision reviews.
- Keep only final accepted image bytes; rejected image bytes remain uncommitted.
- Preserve all result values and 41/35/6/program counts exactly.
- Delete `build_report_figures.py`, `REPORT_FIGURES.json`, and all report-figure
  PDF files.
- `IMAGE2_FIGURES.json` must contain exactly eight entries.
- Commit no credential, endpoint, local absolute path, username, session id,
  proxy detail, or vault pointer.
- Final report remains 28–30 pages, with zero overfull/undefined.
- Do not change runtime, website, benchmark, or evidence JSON values.

---

### Task 1: Build AI Figure Contracts, OCR, and Validation

**Files:**
- Create: `technical_report/figures/validate_ai_figures.py`
- Create: `tests/test_technical_report_ai_figures.py`

**Produces:**
- `FIGURE_CONTRACTS`
- `normalize_ocr(text: str) -> str`
- `normalize_ocr_for_matching(text: str) -> str` (separate, separator-tolerant
  token-matching fallback; never used for provenance)
- `run_tesseract(image: Path) -> dict`
- `validate_figure(root: Path, figure_id: str) -> dict`
- `write_validation_manifest(root: Path) -> dict`
- CLI:
  - `ocr --stem NAME`
  - `validate --stem NAME`
  - `validate-all --write-manifest`

- [ ] Write failing tests for the exact eight ids, dimensions, OCR normalization,
  required data tokens, 2-digest/4-snapshot counts, sidecar paths, and manifest
  hash matching.
- [ ] Confirm RED before implementation.
- [ ] Implement the validator without image-generation or drawing code.
- [ ] Use Tesseract `--psm 6`, `11`, and `12`; retain all raw outputs.
- [ ] Normalize whitespace and Unicode multiplication/dash variants only; never
  normalize numeric digits or decimal points.
- [ ] Parse `review.json`/`content-review.json` as the real
  `image_tool review --out` wrapper: the verdict JSON lives inside a
  top-level string field `"review"` (optionally fenced as `` ```json ... ``` ``),
  not at the sidecar's top level; a missing/non-string/malformed `"review"`
  field must fail closed.
- [ ] Add a separate, separator-tolerant matching fallback
  (`normalize_ocr_for_matching`) that tolerates a lost/substituted `·`
  middle-dot separator and collapses repeated punctuation, but never alters
  digits, decimal points, `%`, `/`, or numeric sign characters; a label
  rescued only via this fallback additionally requires both independent
  vision reviews to confirm its exact original spelling/glyph.
- [ ] Require both data figures to have zero unresolved numeric-token mismatch.
- [ ] Write `AI_FIGURE_VALIDATION.json` from final sidecars.
- [ ] Run unit tests GREEN.
- [ ] Commit:

```bash
git add technical_report/figures/validate_ai_figures.py \
  tests/test_technical_report_ai_figures.py
git commit -m "test(report): define AI figure content contracts" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 2: Author Eight Complete Precision Atlas Prompts

**Files:**
- Create: `technical_report/figures/master_spine.prompt.txt`
- Create: `technical_report/figures/dense_intelligence.prompt.txt`
- Create: `technical_report/figures/system_planes.prompt.txt`
- Replace: `technical_report/figures/argus_architecture.prompt.txt`
- Create: `technical_report/figures/mission_lifecycle.prompt.txt`
- Replace: `technical_report/figures/long_horizon_reliability.prompt.txt`
- Create: `technical_report/figures/public_results.prompt.txt`
- Create: `technical_report/figures/paper_portfolio.prompt.txt`
- Create: `technical_report/figures/ai_figure_review_rubric.txt`
- Create: `technical_report/figures/ai_figure_content_rubric.txt`

- [ ] Copy the shared Blue–Gold Precision Atlas style block into every prompt;
  each prompt must be independently complete.
- [ ] Copy each figure's exact pinned labels and relationships from the spec.
- [ ] For `public_results`, include every exact arena/value/status token from
  `website_results.json`; explicitly prohibit extra numbers and shared scales.
- [ ] For `paper_portfolio`, include all six exact program counts, 41/35/6, and
  `output inventory · not accepted papers`.
- [ ] Require clean horizontal typography optimized for OCR; no curved or
  vertical text.
- [ ] Require spelling exactly as quoted; prohibit invented labels.
- [ ] Add a semantic review rubric and an independent exact-content rubric.
- [ ] Add tests that hash prompt bytes and assert all required tokens appear.
- [ ] Commit:

```bash
git add technical_report/figures/*.prompt.txt \
  technical_report/figures/ai_figure_review_rubric.txt \
  technical_report/figures/ai_figure_content_rubric.txt \
  tests/test_technical_report_ai_figures.py
git commit -m "docs(report): author eight AI figure prompts" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 3: Generate and Accept the Narrative Trio

**Figures:** `master_spine`, `dense_intelligence`, `system_planes`

For each figure:

- [ ] Generate with:

```bash
stem=master_spine  # set to the current figure in this task
python -m argus_skill.tools.image_tool generate \
  --prompt-file "technical_report/figures/${stem}.prompt.txt" \
  --out "technical_report/figures/${stem}.png" \
  --size 1536x1024 --force --timeout 600 --max-retries 3
```

- [ ] Run inspect:

```bash
python -m argus_skill.tools.image_tool inspect \
  --image "technical_report/figures/${stem}.png" \
  > "technical_report/figures/${stem}.png.inspect.json"
```

- [ ] Run Tesseract PSM 6/11/12:

```bash
python technical_report/figures/validate_ai_figures.py ocr --stem "$stem"
```

- [ ] Run semantic vision review:

```bash
python -m argus_skill.tools.image_tool review \
  --image "technical_report/figures/${stem}.png" \
  --prompt-file "technical_report/figures/${stem}.prompt.txt" \
  --rubric "$(cat technical_report/figures/ai_figure_review_rubric.txt)" \
  --out "technical_report/figures/${stem}.review.json" \
  --timeout 600 --max-retries 3
```

- [ ] Run independent exact-content review:

```bash
python -m argus_skill.tools.image_tool review \
  --image "technical_report/figures/${stem}.png" \
  --prompt-file "technical_report/figures/${stem}.prompt.txt" \
  --rubric "$(cat technical_report/figures/ai_figure_content_rubric.txt)" \
  --out "technical_report/figures/${stem}.content-review.json" \
  --timeout 600 --max-retries 3
```

- [ ] Run the validator. It parses `review.json`/`content-review.json` as the
  real `image_tool review --out` wrapper (verdict JSON inside the top-level
  string field `"review"`, plain or fenced) and matches required labels using
  the canonical exact comparison first, falling back to the separator-
  tolerant `normalize_ocr_for_matching` (never loosening digits/decimals/`%`/
  `/`/sign) only when both independent reviews confirm the label's exact
  original spelling/glyph.

```bash
python technical_report/figures/validate_ai_figures.py validate --stem "$stem"
```

- [ ] If any gate fails, record hash/reason in audit provenance, regenerate, and
  repeat; maximum 6 attempts.
- [ ] Sync metadata:

```bash
figure_id=${stem//_/-}
python -m argus_skill.tools.image_tool sync-paper-metadata \
  --project-root . \
  --image "technical_report/figures/${stem}.png" \
  --figure-id "$figure_id" \
  --figure-type architecture \
  --manifest technical_report/figures/IMAGE2_FIGURES.json \
  --prompt-file "technical_report/figures/${stem}.prompt.txt" \
  --review-path "technical_report/figures/${stem}.review.json" \
  --provenance-path "technical_report/figures/${stem}.provenance.json" \
  --allow-noncanonical-prompt
```

- [ ] Require all three accepted before commit.
- [ ] Commit final PNGs and provenance only; rejected PNGs must be absent.

```bash
git add technical_report/figures tests/test_technical_report_ai_figures.py
git commit -m "docs(report): regenerate narrative figures with image-2" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 4: Generate and Accept the Runtime Trio

**Figures:** `argus_architecture`, `mission_lifecycle`,
`long_horizon_reliability`

- [ ] Repeat the Task 3 generation/OCR/two-review/sync loop.
- [ ] Regenerate the two existing AI figures; do not retain old hashes as final.
- [ ] Verify exactly four roles in architecture.
- [ ] Verify lifecycle paths and `replan_requested`.
- [ ] Verify background jobs remain independent in reliability.
- [ ] Maximum 6 attempts per figure; block on failure.
- [ ] Commit accepted outputs/provenance.

```bash
git add technical_report/figures tests/test_technical_report_ai_figures.py
git commit -m "docs(report): regenerate runtime figures with image-2" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 5: Generate and Accept the Two Exact Data Plates

**Figures:** `public_results`, `paper_portfolio`

- [ ] Re-read committed evidence JSON immediately before each generation.
- [ ] Generate `public_results.png` with six separate panels.
- [ ] Require every numeric token from the spec in Tesseract output and both
  vision reviews.
- [ ] Require exactly two `artifact digest` and four `website snapshot`.
- [ ] Reject any extra number.
- [ ] Generate `paper_portfolio.png`.
- [ ] Require all six program counts, 41/35/6, and no acceptance implication.
- [ ] Run up to 12 attempts per data figure; never accept partial correctness.
- [ ] Record rejected hashes/reasons in final provenance without committing
  rejected bytes.
- [ ] Sync both entries to `IMAGE2_FIGURES.json`.
- [ ] Commit accepted outputs/provenance.

```bash
git add technical_report/figures tests/test_technical_report_ai_figures.py
git commit -m "docs(report): regenerate evidence figures with image-2" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 6: Remove Deterministic Figures and Integrate AI PNGs

**Files:**
- Delete: `technical_report/figures/build_report_figures.py`
- Delete: `technical_report/figures/REPORT_FIGURES.json`
- Delete: all six deterministic figure PDF files
- Delete: `tests/test_technical_report_figures.py`
- Modify: report section `.tex` files
- Modify: `technical_report/sections/90_appendix.tex`
- Modify: `tests/test_technical_report_ai_figures.py`
- Generate: `technical_report/figures/AI_FIGURE_VALIDATION.json`
- Regenerate: `technical_report/argus-technical-report.pdf`

- [ ] Replace six `.pdf` references with `.png`.
- [ ] Update captions from `drawn deterministically` to transparent AI/provenance
  wording while preserving claim boundaries.
- [ ] Update appendix provenance: eight image-2 figures, OCR, two vision reviews.
- [ ] Confirm READMEs still resolve `master_spine.png`.
- [ ] Remove deterministic generator, manifest, PDFs, and old tests.
- [ ] Require `IMAGE2_FIGURES.json` exactly equals the expected eight ids.
- [ ] Require all output/prompt/review/OCR/provenance hashes to match.
- [ ] Build report and adjust image widths only if needed for 28–30 pages.
- [ ] Run all AI-figure/narrative/image-tool tests.
- [ ] Commit integration.

```bash
git add technical_report README.md README.zh-CN.md \
  tests/test_technical_report_ai_figures.py \
  tests/test_technical_report_figures.py
git commit -m "docs(report): switch every figure to AI raster" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 7: Full Audit, Independent Review, and Push

- [ ] Run Tesseract/validator fresh for all eight accepted images.
- [ ] Run independent vision review for every figure.
- [ ] Recheck public results and portfolio against JSON.
- [ ] Build report: 28–30 pages, 0 overfull, 0 undefined.
- [ ] Inspect every PDF page and README hero.
- [ ] Verify no report figure PDF, deterministic builder, or
  `REPORT_FIGURES.json` remains.
- [ ] Verify no credential/local path in provenance.
- [ ] Run independent ML-systems and claims/visual reviews.
- [ ] Fix and re-review every Critical/Important finding.
- [ ] Fetch `origin/main`; rebase safely if needed; rerun all gates.
- [ ] Fast-forward push only; never force.
- [ ] Verify remote SHA equals reviewed local HEAD.
