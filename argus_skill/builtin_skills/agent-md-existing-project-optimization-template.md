---
name: AGENTS.md Existing Project Optimization Template
description: Copy-ready AGENTS.md template for repairing or optimizing an existing autonomous EMNLP/ACL long-paper project without erasing useful evidence or gaming validators.
category: project-agent-template
version: 2
---

## Title
AGENTS.md Existing Project Optimization Template

## When to use
- Use this when a project already has research artifacts, code, experiments, logs, LaTeX, figures, reviews, user edits, or a partially accepted EMNLP/ACL paper direction.
- Use it for rescue, hardening, validation cleanup, evidence refresh, paper polish, experiment completion, image-2 figure replacement, format preflight, and final submission-readiness loops.

## When NOT to use
- Do not use this when the operator explicitly rejected the current direction and asked for a clean-slate paper. Use the new-project template instead.
- Do not preserve a bad prototype merely because artifacts exist; if the thesis, benchmark, or paper story is invalid, document the rejection and create a clean-slate reset contract that lists only raw evidence allowed to carry over.
- Direction rule: the operator's most recent explicit instruction wins. Prefer this template when the operator asks to continue, rescue, repair, or optimize the current project, or when current artifacts remain authoritative. Switch to a clean-slate contract only when the operator rejects the current direction or the audit proves the thesis/architecture must be abandoned; raw data may still be selectively listed as allowed input for the new project.

## Copy-ready `AGENTS.md`

```markdown
# AGENTS.md

## Project contract
This is an existing project: an autonomous EMNLP/ACL long-paper workspace. Improve the current paper package while preserving valid source, raw evidence, experiment logs, tests, user edits, and operator-approved decisions. Do not erase history, overwrite user work, or restart from scratch unless the operator explicitly says the current direction is rejected.

The goal is a submission-quality long-paper package, not a pilot PDF, validator-shaped demo, or superficial review-file edit. Keep the artifact pipeline synchronized: research brief -> literature/source discovery -> idea provenance -> benchmark/code -> experiment runs -> result JSON/TSV -> generated tables/figures -> LaTeX -> PDF -> format preflight -> academic-language review -> visual layout review -> submission assurance.

## Binding playbooks and validators
- Read and follow `/home/argustest/research.md` before changing the thesis, benchmark, method name, metric, paper narrative, figure/table design, or final preflight.
- Use `/home/argustest/argus-skill` as the local source for built-in skills and validation commands.
- Prefer `/home/argustest/miniconda3/bin/python` for Argus validation commands.
- Final EMNLP completion requires this exact command to exit 0 and be quoted in completion evidence:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`
- `validate-pipeline`, a compiled PDF, a pilot run, or a passing stale review artifact alone is not final readiness.

## Current operator goal
- Primary improvement objective: [write the current repair/optimization objective]
- Current blocker/frontier: [write the highest-priority failing behavior, validator, metric, or reader-visible issue]
- Success condition: [write the exact command, artifact state, or review outcome that proves this pass is done]
- Out of scope: [write what must not be changed during this optimization pass]
- Allowed reset boundary: [write what, if anything, may be abandoned or carried into a clean-slate reset]

## Canonical state
Before editing, identify and keep synchronized:

| Area | Canonical source | Generated artifacts | Validation/review |
| --- | --- | --- | --- |
| Research/novelty | `research/*` | narrative reports, claim maps | grounding, idea provenance, code reuse validators |
| Benchmark/experiments | benchmark builders, run configs, raw result rows | summaries, tables, plots | manifest checks, uniqueness/leakage checks, statistical tests |
| Paper source | LaTeX/generator/source tables | `paper/main.tex`, `paper/main.pdf`, submission copy | compile, `validate-research-md-format` |
| Figures | image-2 prompts/provenance and data plotting scripts | raster overview, data plots, figure manifest | image review, layout review, artifact manifest |
| Reviews/assurance | current PDF/source hashes and validators | review JSON/MD, calibration, assurance | academic-language, layout, `validate-full-emnlp` |

If generated artifacts and source disagree, treat source/generator plus raw evidence as authoritative. Regenerate downstream artifacts after source changes, refresh manifests, then rerun the relevant review/validator.

## Role model
- Planner: chooses the next blocker with the highest reviewer value, not the easiest cosmetic edit.
- Engineer: fixes one bounded blocker end-to-end, updates generators when needed, and reruns relevant validation.
- Reviewer: verifies the claimed blocker is actually gone and no new blocker was introduced.
- Critic: challenges shortcuts, stale artifacts, duplicated benchmarks, weak novelty, ugly figures, and paper text that only exists to appease validators.
- Scientist: distills reusable lessons only when the fix is complete and general, not from a mid-failure workaround.

## Operating rules
1. Read this file before each new mission or round.
2. Start from the current artifact/log/test frontier, not from memory or old summaries.
3. Preserve unrelated user edits. Never revert or rewrite files outside the current blocker without a clear reason.
4. Prefer generator/source fixes over direct edits to generated output.
5. Keep freshness chains synchronized: source -> generated artifact -> manifest -> review -> final validator.
6. Do not mark reports, reviews, calibration, or readiness files as passing until the actual artifact passes the underlying check.
7. If an automated review is stale, refresh it after rebuilding the artifact; do not edit review JSON by hand to force a pass.
8. If a command fails, inspect and fix the failure. Do not hide the failure behind a fallback unless the fallback is explicitly part of the design.
9. Treat reader-visible quality as part of correctness. A validator-passing but ugly, under-evidenced, or incoherent paper is not done.

## Optimization workflow
1. Snapshot the current frontier: daemon status, recent logs, changed files, failing tests/validators, current PDF, current reviews, and most recent generated artifacts.
2. Pick one bounded blocker and write its acceptance criteria.
3. Locate all source surfaces that can generate or invalidate the blocker.
4. Apply the minimal complete fix in source/generator/raw evidence.
5. Regenerate affected artifacts in dependency order.
6. Refresh manifests, reviews, and preflight artifacts whose source/PDF hashes changed.
7. Run targeted validation for the blocker.
8. Run broader validation if the change affects shared source, public behavior, paper readiness, experiment claims, figure provenance, or review hashes.
9. Stop only when the blocker is gone or when a new operator decision is required.

## Existing research and evidence repair
1. Preserve valid raw results and provenance, but do not preserve weak claims, stale reviews, copied text, duplicated benchmark rows, or known-invalid benchmark framing.
2. If the current evidence is only pilot-scale, label it as pilot evidence and queue a real scale-up run; do not pad the paper or assurance files into final readiness.
3. If the local validator requires at least 240 unique semantic scored main tasks/episodes for final long-paper evidence, then 50--60 tasks are pilot evidence even if all methods ran successfully.
4. Benchmark scale must come from unique semantic tasks/examples, not duplicates, relabeling, suffixes, paraphrase inflation, or shuffled copies.
5. Benchmark/source selection must document source diversity, recency/relevance, adoption/rejection decisions, license/access status, leakage controls, and why each source tests a distinct capability.
6. Every numeric claim must remain tied to current raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
7. If the method-positive thesis is rejected by evidence, queue repair/pivot tasks for method, metric, benchmark, or objective. Do not convert it into a negative-result paper unless the operator explicitly asks.

## Existing paper repair
1. Improve the artifact the reader/reviewer sees, not just the validator surface. Reader-facing prose must stay clear, specific, and evidence-backed.
2. Preserve useful author/user edits unless they contradict evidence, current validators, or the operator's latest direction.
3. Do not convert uncertainty into repetitive caveats that make the paper worse. Move detailed scope limits to limitations/discussion.
4. Keep the one-sentence contribution concrete: "We propose X. We show X improves Y by Z because W." If X/Y/Z/W cannot be backed by current artifacts, fix evidence or claims before language polish.
5. The abstract should read like a normal EMNLP abstract. Do not mention validator names, raw paths, evidence-span bookkeeping, review mechanics, or appendix layout trivia in the abstract/body.
6. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.
7. Use official ACL/EMNLP review style, anonymous author block, 7.5--8.0 pages of main content, conclusion by the end of page 8, Limitations and Ethical Considerations after Conclusion, References before Appendix, and a reproducibility appendix.
8. Run `validate-research-md-format` after the final compile and before academic-language/layout review. Update `paper/FORMAT_PREFLIGHT.md` with compile status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
9. Do not tolerate undefined refs/citations, rendered `[?]`, `Overfull \hbox > 5pt`, placeholders, `% UNVERIFIED`, code-like display labels, missing numerical table captions, or stale PDF/log/preflight facts.

## Figure repair
1. Use image-2/codex-image2 for core conceptual figure repair.
2. Figure 1, teaser, overall, and core method/framework/system/pipeline overview figures must be the actual generated image-2 raster `output_path`, or equivalent codex-image2 raster, included directly from `paper/main.tex`.
3. If the overview is ugly, cramped, misspelled, or low quality, regenerate through image-2 with a better prompt. Do not locally redraw, trace, vectorize, PDF-wrap, screenshot, crop, downsample, resave, or overwrite it after provenance is written.
4. Do not replace the overview with matplotlib/FancyBboxPatch, TikZ node graphs, PIL/SVG/HTML canvases, manual vector tools, cleaned PDF derivatives, or a locally drawn mockup labeled as image-2.
5. Preserve prompt, metadata, generation provenance, inspect/review artifacts, SHA-256, width, and height. Refresh `paper/figures/IMAGE2_FIGURES.json` when the prompt, provenance, generation settings, accepted output, or paper include path changes.
6. Do not regenerate an already accepted image merely to refresh metadata; repair missing provenance from recorded facts when possible, otherwise regenerate once through image-2.
7. Conceptual figures should be adaptive or landscape page-width assets, preferably `1536x1024` or `1920x1080`; avoid square `1024x1024`, weird/sketchy fonts, tiny text, heavy gradients, photorealism, excessive logos, and decorative clutter.
8. Data figures and tables must derive from local raw data/results and their scripts, not from image-2.

## Exemplar/style repair
1. If `paper/style_ref/EXEMPLAR.json` is absent, URL-only, stale, or schema-incomplete, invoke the Paper Exemplar PDF Learning skill before paper prose polish.
2. Ensure at least two open-access top-conference exemplar PDFs exist under `paper/style_ref/exemplars/<slug>/paper.pdf`, with extracted text, `pdf_sha256`, license, `pdf_storage_policy`, `usage: "structural_style_only"`, and `no_prose_copy: true`.
3. Refresh `paper/style_ref/STYLE_PROFILE.md` when the target venue, paper structure, method/evaluation style, or exemplar set changes.
4. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`; URL-only exemplars remain blockers.
5. Use exemplars only for structure. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

## Final review and assurance repair
1. After content and PDF are stable, run:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`
   then validate `paper/ACADEMIC_LANGUAGE_REVIEW.json` with `validate-academic-language-review`.
2. After final compile, run:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write`
   then validate `paper/LAYOUT_REVIEW.json` with `validate-layout-review`.
3. Academic-language and layout review scores must be at least 4/5, fresh, backed by the required model/vision mode, and have `needs_revision: false`.
4. Write or refresh `paper/SUBMISSION_ASSURANCE.md` and `paper/SUBMISSION_ASSURANCE.json` only from current validator/review results.
5. Review artifacts, calibration files, and readiness reports are evidence, not targets. Never hand-edit them to say PASS/ready while the underlying paper, PDF, image, evidence, or validator remains blocked.
6. Never emit PASS/WARN/final-ready if any required validator fails. `WARN` cannot launder pilot scale, stale reviews, missing provenance, or hard blockers into final EMNLP readiness.

## Telemetry and long-run visibility
1. Long experiments must expose live progress in `progress.jsonl`, `status.json`, logs, and a run manifest.
2. Progress records should include timestamp, run id, method/baseline, completed tasks, total tasks, success/failure counts, current phase, last heartbeat, estimated remaining work when available, and latest artifact path.
3. If a daemon starts a long model call, compile, image generation, or benchmark run, keep a heartbeat or status update so the operator can distinguish real progress from an idle hang.
4. Respect a STOP-file cancellation contract and record whether a stop was clean, partial, or failed.

## Forbidden shortcuts
- Do not restart from scratch because the current blocker is hard.
- Do not overwrite generated artifacts without updating the generator/source.
- Do not hand-edit manifests, reviews, calibration, or readiness files to contradict source or validator output.
- Do not remove tests, citations, figures, benchmark cases, or paper sections solely to avoid a failure.
- Do not claim a blocker is fixed while a stale artifact is still being validated.
- Do not replace image-2 conceptual figures with local redraws.
- Do not satisfy academic-language review by making the writing bland, repetitive, defensive, or non-paper-like.

## Completion contract
An optimization task is complete only when:
- the selected blocker is fixed in source and regenerated artifacts,
- source, generated artifacts, manifests, reviews, and validation reports are synchronized,
- relevant targeted validation passes,
- broader validation was run when the change affects final paper readiness,
- remaining failures are newly enumerated and not caused by the change,
- the handoff states the current frontier and next highest-priority blocker.

The full project is complete only when the final `validate-full-emnlp` command above exits 0 on the current workspace and that exact output is quoted in completion evidence.
```

## Generality check
This template is EMNLP/ACL-paper-specific but must stay project-neutral. It may contain stable local Argus paths and validation commands, but it must not contain a specific project title, benchmark name, result number, figure name, or prior-workspace story.

## Coverage check
Before using the template, fill the current operator goal, canonical state table, validation commands, and reset boundary from the actual project. Delete no hard gate unless the operator explicitly changes the paper scope.
