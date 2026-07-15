# AI Redraw of the Structural Argus Report Figures — Implementation Plan

**Goal:** Regenerate the six structural/concept report/README figures as newly
generated, fully AI-rendered Blue–Gold Precision Atlas PNGs (validated by OCR,
two vision reviews, hashes, and content contracts), while keeping the two data
figures (`public_results`, `paper_portfolio`) deterministically drawn, exact,
and blue-gold to match.

**Architecture:** A validator defines exact per-figure content contracts and
collects Tesseract/vision evidence for the six structural figures. Six complete
standalone prompts drive `gpt-image-2`. After all six pass, the structural
deterministic drawing code/assets are removed, LaTeX switches the six structural
references to PNG, and `IMAGE2_FIGURES.json` becomes the manifest for the six
AI structures. The two data figures keep `build_report_figures.py` (trimmed to
only them) and `REPORT_FIGURES.json` (trimmed to two entries).

**Tech Stack:** `argus_skill.tools.image_tool`, `gpt-image-2`, vision review
model, Tesseract OCR, Python 3.11, pytest, LaTeX, SHA-256.

## Global Constraints

- Approved spec:
  `docs/superpowers/specs/2026-07-15-ai-redraw-structural-report-figures-design.md`.
- Work only in the isolated
  `/home/argustest/.copilot/session-state/7ab35eb1-e9e4-411f-98f5-9ddb9b5fd70b/pro-readme-worktree`.
- The six structural figures must be newly generated AI rasters; the two data
  figures remain deterministic.
- Structural generator must be `gpt-image-2`; structural output must be
  1536×1024 PNG.
- No manual pixel edits, deterministic text/chart overlay, compositing, SVG,
  TikZ, Matplotlib, or vector replacement for the six structural figures.
- Wrong text, number, role, arrow, or relationship in a structural figure means
  reject and regenerate.
- Keep only final accepted structural image bytes; rejected image bytes remain
  uncommitted.
- Preserve all result values and 41/35/6/program counts exactly.
- Keep `build_report_figures.py` for the two data figures; during integration,
  remove only its structural drawing functions and structural PDFs.
- `IMAGE2_FIGURES.json` must contain exactly six structural entries;
  `REPORT_FIGURES.json` must contain exactly the two data entries.
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
- `FIGURE_CONTRACTS` (the six structural figures)
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

- [ ] Write failing tests for the exact six structural ids, dimensions, OCR
  normalization, required labels, sidecar paths, and manifest hash matching.
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
- [ ] Write `AI_FIGURE_VALIDATION.json` from final sidecars.
- [ ] Run unit tests GREEN.
- [ ] Commit.

---

### Task 2: Author Six Complete Precision Atlas Prompts

**Files:**
- Create: `technical_report/figures/master_spine.prompt.txt`
- Create: `technical_report/figures/dense_intelligence.prompt.txt`
- Create: `technical_report/figures/system_planes.prompt.txt`
- Replace: `technical_report/figures/argus_architecture.prompt.txt`
- Create: `technical_report/figures/mission_lifecycle.prompt.txt`
- Replace: `technical_report/figures/long_horizon_reliability.prompt.txt`
- Create: `technical_report/figures/ai_figure_review_rubric.txt`
- Create: `technical_report/figures/ai_figure_content_rubric.txt`

- [ ] Copy the shared Blue–Gold Precision Atlas style block into every prompt;
  each prompt must be independently complete.
- [ ] Copy each figure's exact pinned labels and relationships from the spec.
- [ ] Require clean horizontal typography optimized for OCR; no curved or
  vertical text.
- [ ] Require spelling exactly as quoted; prohibit invented labels.
- [ ] Add a semantic review rubric and an independent exact-content rubric; both
  apply to the six structural (concept) figures. No data-figure numeric/source
  clauses.
- [ ] Add tests that hash prompt bytes and assert all required tokens appear.
- [ ] Commit.

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
- [ ] Sync metadata to `IMAGE2_FIGURES.json` (figure type `architecture`).
- [ ] Require all three accepted before commit.
- [ ] Commit final PNGs and provenance only; rejected PNGs must be absent.

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
- [ ] Commit accepted outputs/provenance. After Task 4, all six structural
  figures are AI-generated and `IMAGE2_FIGURES.json` has exactly six entries.

---

### Task 5: Validate and Restyle the Two Deterministic Data Charts

**Figures:** `public_results`, `paper_portfolio`

No AI generation, no image-model call, and no OCR gate for these figures.

- [ ] Confirm both figures are drawn deterministically by
  `build_report_figures.py` from the committed evidence JSON.
- [ ] Rebuild them deterministically and confirm exact, reproducible digests.
- [ ] Restyle/verify they use the shared Blue–Gold palette so they match the six
  AI structures visually.
- [ ] Verify every public value and portfolio total equals the committed
  evidence JSON exactly (via the deterministic-figure tests).
- [ ] Ensure `REPORT_FIGURES.json` records exactly these two entries with their
  reproducible digests.
- [ ] Do not add these figures to `IMAGE2_FIGURES.json`.
- [ ] Commit any restyle/manifest updates.

---

### Task 6: Integrate Six AI Structures + Two Deterministic Data Figures

**Files:**
- Modify: `technical_report/figures/build_report_figures.py` (remove structural
  drawing functions; keep only the two data figures)
- Modify: `technical_report/figures/REPORT_FIGURES.json` (exactly two data
  entries)
- Delete: the six structural deterministic figure PDF/PNG drawings and any
  structural drawing helpers
- Modify: `tests/test_technical_report_figures.py` (assert only the two data
  figures remain deterministic)
- Modify: report section `.tex` files
- Modify: `technical_report/sections/90_appendix.tex`
- Modify: `tests/test_technical_report_ai_figures.py`
- Generate: `technical_report/figures/AI_FIGURE_VALIDATION.json`
- Regenerate: `technical_report/argus-technical-report.pdf`

- [ ] Switch the six structural figure references to their accepted `.png` AI
  rasters; keep the two data figure references on their deterministic `.png`.
- [ ] Update captions from `drawn deterministically` to transparent
  AI/provenance wording for the six structural figures only; keep the two data
  figures' deterministic-provenance captions accurate.
- [ ] Update appendix provenance: six image-2 structural figures (OCR, two
  vision reviews) plus two deterministic data figures.
- [ ] Confirm READMEs still resolve `master_spine.png`.
- [ ] Remove structural drawing functions/PDFs from the deterministic generator;
  trim `REPORT_FIGURES.json` to the two data entries.
- [ ] Require `IMAGE2_FIGURES.json` exactly equals the six structural ids and
  `REPORT_FIGURES.json` exactly equals the two data ids.
- [ ] Require all structural output/prompt/review/OCR/provenance hashes to match.
- [ ] Build report and adjust image widths only if needed for 28–30 pages.
- [ ] Run all AI-figure/narrative/deterministic-figure/image-tool tests.
- [ ] Commit integration.

---

### Task 7: Full Audit, Independent Review, and Push

- [ ] Run Tesseract/validator fresh for all six accepted structural images.
- [ ] Run independent vision review for every structural figure.
- [ ] Rebuild the two deterministic data figures and recheck values against JSON.
- [ ] Build report: 28–30 pages, 0 overfull, 0 undefined.
- [ ] Inspect every PDF page and README hero.
- [ ] Verify `IMAGE2_FIGURES.json` has exactly six structural entries and
  `REPORT_FIGURES.json` has exactly the two data entries; no structural
  deterministic PDF or structural drawing helper remains.
- [ ] Verify no credential/local path in provenance.
- [ ] Run independent ML-systems and claims/visual reviews.
- [ ] Fix and re-review every Critical/Important finding.
- [ ] Fetch `origin/main`; rebase safely if needed; rerun all gates.
- [ ] Fast-forward push only; never force.
- [ ] Verify remote SHA equals reviewed local HEAD.
