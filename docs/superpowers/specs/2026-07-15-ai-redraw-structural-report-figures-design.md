# AI Redraw of the Structural Argus Report Figures — Design Specification

**Date:** 2026-07-15
**Status:** Approved
**Scope:** The six structural/concept figures in Technical Report 0.3 and the
README hero. The two data figures (`public_results`, `paper_portfolio`) are
explicitly out of scope for AI generation and remain deterministically drawn.

## Goal

Regenerate the six structural/concept figures as newly generated AI rasters,
and never hand-draw them again. The two data figures are exact evidence charts
and MAY remain deterministically drawn; they are not regenerated with an image
model. All eight visible figures share one Blue–Gold Precision Atlas art
direction so the AI structures and the deterministic data plates match.

The six AI-regenerated structural figures are:

1. `master_spine.png`
2. `dense_intelligence.png`
3. `system_planes.png`
4. `argus_architecture.png`
5. `mission_lifecycle.png`
6. `long_horizon_reliability.png`

The two figures that remain deterministically drawn are:

7. `public_results.png`
8. `paper_portfolio.png`

Only the six structural figures are AI-regenerated (including the two that were
previously image-2 figures). The two data figures stay deterministic, exact,
and blue-gold.

## Non-Negotiable Constraints

- Structural-figure generator: `gpt-image-2` through
  `argus_skill.tools.image_tool`.
- Structural output: PNG raster, 1536×1024 landscape (3:2), no deterministic
  overlay.
- Data figures keep their existing deterministic generator
  (`build_report_figures.py`) and remain exact, source-grounded, and rendered
  in the same Blue–Gold palette.
- Shared palette:
  - bone white `#FBFAF6`;
  - system blue `#315BCE`;
  - deep blue `#214884`;
  - frontier/evidence gold `#C38A20`;
  - graphite `#24272B`.
- Style: museum-grade technical atlas, fine linework, generous negative space,
  restrained blue-gold accents, professional report quality.
- No cyberpunk, neon, robots, brains, faces, fake 3D dashboards, vendor logos,
  watermarks, or marketing badges.
- For the six structural figures: no manual correction, compositing, text
  overlay, chart overlay, or pixel edit. If a number, label, arrow, role, or
  relationship is wrong, reject the entire raster and regenerate it.
- Public result values and portfolio counts must remain exactly equal to the
  committed evidence JSON files.
- The final report remains 28–30 pages, with zero overfull boxes and zero
  undefined references/citations.

## Figure-by-Figure Semantic Contracts

### 1. Master Spine

**Purpose:** The report's primary causal story.

Required exact labels:

- `Every run expands the frontier.`
- `Unknown objective`
- `Dense Intelligence Runtime`
- `Evidence Gate`
- `Runtime Evolution`
- `Expanded OOD Frontier`
- `Manager`
- `Planner`
- `Engineer`
- `Reviewer`
- `Memory`
- `Skills`
- `Tools`
- `Verifiers`
- `Routing`
- `Evaluations`
- `model parameters remain fixed`
- `capability is not guaranteed to grow every run`

Required relationships:

- One left-to-right causal path across the five stages.
- The four roles are inside Dense Intelligence Runtime.
- Runtime Evolution contains the six capability-state components.
- One return path from Expanded OOD Frontier to the next unknown objective.
- Evidence Gate is the only gold decision point; runtime structure is blue.

### 2. Dense Intelligence

**Purpose:** Explain continuity without asserting human superiority.

Required exact labels:

- `Dense Intelligence`
- `Episodic research`
- `Argus Life`
- `decision`
- `execution`
- `verification`
- `state retention`
- `conceptual model · not a reported benchmark`

Required relationships:

- Episodic research shows gaps and context recovery.
- Argus Life shows the four elements remaining coupled over time.
- No `Argus > human`, superiority score, or fake measurement.

### 3. Three Planes

**Purpose:** Explain implementation ownership.

Required exact labels:

- `Control Plane`
- `Execution Plane`
- `Evidence Plane`
- `Manager`
- `Planner`
- `LifeSupervisor`
- `SkillLoop`
- `Engineer`
- `Reviewer`
- `Run Gateway`
- `Event Tape`
- `Usage Ledger`
- `Credential Redaction`
- `Provenance`
- `112 typed events`

Required relationships:

- Control dispatches into Execution.
- Execution emits into Evidence.
- Evidence is read back by Control and Execution but does not decide.

### 4. Argus Architecture

**Purpose:** Present the four-role persistent runtime.

Required exact labels:

- `Argus`
- `Operator objective`
- `Persistent research runtime`
- `Manager`
- `Planner`
- `Engineer`
- `Reviewer`
- `Manager: front door and stage authority`
- `Reviewer: completion authority`
- `Inspectable artifacts and evidence`

Required relationships:

- One objective enters one runtime.
- Exactly four role modules appear.
- Artifacts and evidence exit to the operator.
- Runtime services remain subordinate to role judgment.

### 5. Mission Lifecycle

**Purpose:** Explain durable lifecycle and recovery.

Required exact labels:

- `Claim backlog item`
- `pending → running`
- `Run mission`
- `Engineer ↔ Reviewer`
- `bounded session reuse`
- `Reviewer verdict`
- `done`
- `continue`
- `Plan next work`
- `Backlog / continuous`
- `paused`
- `blocked`
- `replan_requested`
- `drain to mission boundary`

Required relationships:

- Claim → mission → verdict.
- Continue returns to mission.
- Done reaches certified output.
- Replan returns to Planner, not completion.
- Paused/blocked are explicit exits.
- Daemon drain does not cut through a live mission.

### 6. Long-Horizon Reliability

**Purpose:** Explain liveness, decision progress, and safe exit.

Required exact labels:

- `Argus long-horizon cycle`
- `Planner`
- `Engineer`
- `Reviewer`
- `Checkpoint`
- `Decision progress`
- `Supervised background jobs`
- `run independently`
- `Safe round boundary`
- `No new decision`
- `1,800 s decision budget`
- `Return to Planner`
- `Budget`
- `Event log`
- `Artifacts`
- `Process liveness`

Required relationships:

- Reviewer emits the checkpoint and decision-progress state.
- Background jobs remain on an independent lane.
- No-decision/budget triggers enter Safe round boundary.
- Safe round boundary returns to Planner.
- Budget, Event log, and Artifacts persist across the cycle.

### 7. Public Results (deterministic data figure)

**Purpose:** Present six public arenas as exact scoped proof points.

This figure is **not** AI-generated. It is drawn deterministically by
`build_report_figures.py` from `technical_report/evidence/website_results.json`,
in the shared Blue–Gold palette, with exact bars/labels and no image-model
call. Its content contract is defined and validated by that generator and the
existing deterministic-figure tests, not by the AI validator.

Required exact arena/value tokens:

- `NVIDIA SOL-ExecBench`
- `Global #6`
- `2× #1`
- `7 top-3`
- `nanochat · B200`
- `0.9636 BPB`
- `Human SOTA 0.9646`
- `nanochat · H100`
- `0.9855 BPB`
- `Human SOTA 0.9879`
- `nanoGPT speedrun`
- `79.77 s`
- `Human #83 80.18 s`
- `AARRI-Bench`
- `63/82`
- `76.8%`
- `Paper best 68.3%`
- `Arbor · RUC NLPIR`
- `28.0 gap`
- `Arbor 20.83`
- `Claude Code 8.33`
- `Codex 6.25`
- exactly two `artifact digest` labels;
- exactly four `website snapshot` labels.

Required visual structure:

- Six clearly separated small-multiple panels.
- Units remain panel-local; no shared normalized scale.
- B200/nanoGPT panels carry artifact-digest status.
- Other four panels carry website-snapshot status.
- The figure must not add a universal-SOTA headline.

### 8. Paper Portfolio (deterministic data figure)

**Purpose:** Present the de-duplicated research-output inventory.

This figure is **not** AI-generated. It is drawn deterministically by
`build_report_figures.py` from `technical_report/evidence/paper_inventory.json`,
in the shared Blue–Gold palette, with exact counts and no image-model call.

Required exact labels:

- `Research Portfolio`
- `41 papers`
- `35 manuscripts`
- `6 drafts`
- `Multimodal & Vision-Language Models 16`
- `Cognitive Bias in LLMs 9`
- `Efficiency, Compression & Decoding 7`
- `LLM Agent Methods 5`
- `World Models 2`
- `State Trace & Auditability 2`
- `output inventory · not accepted papers`

Required relationships:

- Six program groups.
- Manuscript/draft status is visually distinguished.
- Totals sum visibly to 41.
- No acceptance or publication-status badge.

## Generation Workflow (six structural figures only)

Each of the six structural figures uses a complete standalone prompt file:

```text
technical_report/figures/<stem>.prompt.txt
```

For each attempt:

1. Generate with `image_tool generate`.
2. Run local dimension/hash inspection with `image_tool inspect`.
3. Run Tesseract OCR using multiple page-segmentation modes.
4. Normalize OCR whitespace and common multiplication-symbol variants without
   altering the image (canonical `normalize_ocr`, used for provenance and
   exact matching). A separate, more tolerant matching-only normalization
   additionally accepts a lost/substituted `·` separator (see "OCR and
   Vision Acceptance" below) without loosening digits, decimals, `%`, `/`,
   or numeric signs.
5. Compare extracted tokens against the figure's required token contract.
6. Run a vision-capable semantic/content review with the complete prompt via
   `image_tool review --out <stem>.review.json`.
7. Run a second, independent exact-content vision review for every structural
   figure via `image_tool review --out <stem>.content-review.json`.
8. Reject and regenerate on any material mismatch.

Attempt limits:

- Concept/architecture figures: at most 6 attempts per figure.
- Hitting the limit is `BLOCKED`, never permission to accept an incorrect
  image.

Rejected attempts are not committed. Final provenance records attempt count and
the hashes/rejection reasons of discarded attempts without retaining their image
bytes.

The two data figures do not use this workflow: they are rebuilt deterministically
and validated by their generator and the deterministic-figure tests.

## OCR and Vision Acceptance (six structural figures only)

Every final structural figure stores:

- `<stem>.ocr.txt`: raw Tesseract output;
- `<stem>.ocr.json`: expected tokens, normalized observed tokens, coverage, and
  unresolved mismatches;
- `<stem>.review.json`: semantic/visual review;
- `<stem>.content-review.json`: independent exact-content review;
- `<stem>.inspect.json`;
- `<stem>.provenance.json`;
- generation sidecar `<stem>.png.json`;
- prompt `<stem>.prompt.txt`.

`<stem>.review.json` and `<stem>.content-review.json` are written verbatim by
`image_tool review --out`, whose real output is a wrapper object
(`{"image": ..., "model": ..., "endpoint": ..., "prompt": ..., "rubric": ...,
"review": "<model text>"}`): the actual verdict (`keep_or_regenerate`,
`confirmed_labels`, ...) lives inside the top-level *string* field `"review"`,
optionally fenced as `` ```json ... ``` ``, not at the sidecar's top level. The
validator parses this wrapper directly — no manual "flatten" step is required
or permitted. A missing, non-string, or malformed `"review"` field fails
closed (the figure cannot pass).

Required short labels are matched against OCR using the canonical, exact
`normalize_ocr` comparison first. If that fails, a separate, separator-tolerant
fallback (`normalize_ocr_for_matching`) may match a label whose only difference
is a lost or substituted `·` (middle-dot) separator between two label halves
(e.g. `Backlog / continuous`) — this fallback never loosens digits, decimal
points, `%`, `/`, or numeric sign characters, so `1,800 s` can never match
`1,80 s` and `112 typed events` is never loosened. A label rescued only through
this separator-tolerant fallback additionally requires BOTH independent vision
reviews to confirm the label's exact original spelling/glyph (via the canonical,
non-tolerant comparison) before it counts as present; one review confirming it
alone is never sufficient. A required short label may also be accepted when BOTH
independent vision reviews explicitly confirm it, even if OCR missed it; one
review confirming it alone never suffices.

Structural figures pass when:

- all required entities and relationships are confirmed by vision review;
- required short labels pass OCR (exact or separator-tolerant-plus-both-
  reviews) or are explicitly confirmed by both independent vision reviews;
- no prohibited content appears.

## Provenance

`technical_report/figures/IMAGE2_FIGURES.json` is the manifest for the six
AI-generated structural figures and contains exactly six entries. Each entry
includes:

- figure id/type;
- output path/hash/dimensions;
- generator/model;
- prompt path/hash;
- inspect/review/content-review paths and hashes;
- OCR paths/hashes and coverage;
- generation attempt count;
- rejected-attempt hashes and concise rejection reasons.

`technical_report/figures/REPORT_FIGURES.json` is the manifest for the two
deterministic data figures and contains exactly two entries (`public_results`,
`paper_portfolio`), each recording the deterministic PNG (and any PDF) output
and its reproducible digest.

No credential, local absolute path, username, session id, proxy route, or vault
pointer may appear in committed provenance.

## Deterministic Data Generator

`build_report_figures.py` is **kept**, but reduced to only the two data
figures. During integration (Task 6), all structural drawing functions and the
structural `.pdf` outputs are removed from it, so it produces only
`public_results` and `paper_portfolio`. `REPORT_FIGURES.json` is trimmed to
those two entries. The deterministic data figures and their tests continue to
enforce exactness and the shared Blue–Gold palette.

The six structural figures' previous deterministic `.pdf`/`.png` drawings and
any structural drawing code are removed once their AI rasters are accepted. No
hidden deterministic chart/reference image is retained for the structural
figures.

## Report and README Integration

- Structural figure references switch to the accepted `.png` AI rasters.
- Data figure references remain the deterministic `.png` outputs.
- Keep current figure ids, captions, and semantic claims unless a new raster
  requires a caption clarification.
- README English and Chinese continue to reference `master_spine.png`, now the
  AI raster.
- Preserve the 30-page report envelope; adjust figure width only, not evidence
  prose, to recover layout.
- Rebuild and visually inspect all 30 pages.

## Test and Review Gates

Automated tests must verify:

- `IMAGE2_FIGURES.json` contains exactly six expected structural ids;
- `REPORT_FIGURES.json` contains exactly the two deterministic data ids;
- each structural output and sidecar hash matches;
- all structural outputs are 1536×1024 PNG;
- structural OCR/label contracts pass;
- the deterministic data figures remain exact and reproducible;
- all report/README image references resolve to a manifest entry;
- all public values and portfolio totals remain unchanged.

Final reviews:

1. Independent figure-by-figure visual/semantic review of the six structures.
2. Independent exact-content/OCR review of the six structures.
3. Full report claims/citations/visual review.
4. Full ML-systems/source-grounding review.

No structural figure is accepted with a Critical or Important finding.
