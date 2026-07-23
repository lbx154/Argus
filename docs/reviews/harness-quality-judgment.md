# Removing harness quality/keyword judgment (T1 + T2)

Design philosophy enforced: the harness is domain-agnostic plumbing (budget,
persistence, scheduling, structured I/O, anti-fabrication integrity rails). It
must NOT encode keyword/regex/hardcoded judgments about science or "what is a
good paper". Quality is the agent's call against an explicit checklist. *The
harness is never smarter than the agents.*

## T1 — project structure belongs to the agent

- Harness-owned project bootstrap was removed entirely. The daemon no longer
  scans an empty root, creates a synthetic setup mission, writes research
  artifacts, injects `AGENTS.md`, creates `.venv`, or copies helper code.
- The memory backend no longer materializes a deterministic research skeleton.
- Manager vertical selection persists only framework state; repository layout,
  dependencies, helper code, and research artifacts are created by the agent as
  part of the real mission.

## T2 — review skills stop emitting authoritative quality verdicts

The three model/vision review skills computed a deterministic regex/heuristic
assessment and shipped an authoritative `verdict` PASS/FAIL/BLOCKED +
`score_1_to_5` + `needs_revision` (gated by `score < threshold`) that the agent
was told to trust. Converted to a facts-only schema (v2):

- `harness_verdict: null`, `decision_authority: "agent_checklist"`,
  `no_harness_quality_verdict: true`.
- `structural_status: "ok"|"blocked"` reflects ONLY whether the tool could
  produce facts (missing source / un-renderable PDF / model unavailable). CLI
  exit code keys off `structural_status`, never a quality verdict.
- Neutral `facts` (e.g. `abstract_word_count`, `section_titles`, `page_count`,
  `conclusion_page`, `references_page`) — measurements, not judgments.
- The model/vision reviewer (an agent) is still invoked; its raw output is
  surfaced under `model_review` / `vision_review` as ADVISORY input, not gated.

Files: `academic_language_review.py`, `paper_layout_review.py`,
`paper_infrastructure_review.py`. The infrastructure reviewer keeps leak
detection as a narrow publication-safety FINDING (`leak_free`, `leak_findings`,
`checked_scope`), not a quality score.

### Preserved (NOT removed)

- All STRUCTURAL blocking (missing `main.tex`/source/PDF/snapshots, model error).
- Publication-integrity findings: TODO/TBD/placeholder markers, infra/secret
  leak detection.
- Evidence integrity remains the Reviewer's responsibility against current
  artifacts and the active stage checklist.

### Pruned

- Machine-authored quality taxonomies and repair-mode routing were removed;
  Reviewer judgment and vertical-owned checklists now carry that responsibility.

### Migrated to the agent checklist

`stage_machine.py` already covered section order incl. Conclusion placement,
references on page 9+, 7.5–8.0 body pages, no overfull hbox, figures from raw
results, infra-leak grep, placeholders, table style, citations. Added a new
`review.language` item covering abstract shape, introduction roadmap / cited
gap / quantified preview, method readability, and evidence-tied claims — so the
prose-quality concern removed from the harness is now an explicit list the
reviewer agent self-verifies (the academic-language reviewer is advisory input).

## Follow-ups (not done here)

- `skills/paper_calibration.py` (2324 lines) is currently UNWIRED — nothing
  imports `detect_quality_blockers` / `validate_quality_calibration_file`. It
  mixes a hardcoded quality-calibration verdict machinery
  (`QUALITY_CALIBRATION_VERDICTS`, `READY_VERDICTS`, quality_signals,
  negative-regression patterns) with dormant anti-fabrication checks
  (results↔claim consistency, planned-vs-executed scale, benchmark provenance,
  duplicate-expansion). Recommended follow-up: move the anti-fabrication subset
  next to `evidence_chain.py` as a neutral evidence-integrity module and drop
  the quality-calibration verdict premise. Left intact for now to avoid
  deleting anti-fabrication capability (per review of c6b11d3).
- Dead helpers left in the converted review skills (`_merge_model_review`,
  `_final_score`, `_revision_directives`, `_vision_issues`,
  `_vision_score_should_block`, section/criterion scoring helpers) can be
  removed in a focused cleanup pass.

## Verification

- Full test suite green; converted files lint-clean (the one remaining ruff
  finding in `stage_machine.py` import ordering is pre-existing).
- Test-induced `paper/artifacts/slm_llm_human_hierarchy.json` churn reverted.
