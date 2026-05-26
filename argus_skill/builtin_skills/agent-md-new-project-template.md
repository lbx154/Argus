---
name: AGENTS.md New Project Template
description: Copy-ready AGENTS.md template for starting a clean-slate autonomous EMNLP/ACL long-paper project without inheriting prior paper assumptions.
category: project-agent-template
version: 2
---

## Title
AGENTS.md New Project Template

## When to use
- Use this when creating a new autonomous research workspace whose deliverable is an EMNLP/ACL-style long-paper package.
- Use it before the daemon chooses the final thesis, benchmark, method name, paper story, figure design, or completion criteria.

## When NOT to use
- Do not use this to continue or repair an existing paper with valuable artifacts, tests, user edits, or an operator-approved direction. Use the existing-project optimization template instead.
- Do not fill it with copied titles, claims, benchmark choices, result numbers, figures, or generated artifacts from another project.
- Direction rule: the operator's most recent explicit instruction wins. Use this template when the operator rejects the current direction or asks for a fresh start. If raw data, logs, or evidence from an older project should remain usable, list them as allowed inputs; do not preserve the older thesis, architecture, benchmark framing, or paper story by default.

## Copy-ready `AGENTS.md`

```markdown
# AGENTS.md

## Project contract
This workspace must produce a submission-quality EMNLP/ACL long-paper package, not a pilot PDF, validator-shaped demo, or renamed copy of an older project. Build the paper as an evidence pipeline: research brief -> literature/source discovery -> idea provenance -> benchmark/code -> experiment runs -> result JSON/TSV -> generated tables/figures -> LaTeX -> PDF -> format preflight -> academic-language review -> visual layout review -> submission assurance.

This is a clean-slate project. Do not inherit titles, claims, datasets, benchmark episodes, generators, figures, review artifacts, result numbers, architecture, thesis, or paper story from any prior project unless they are listed in **Allowed starting inputs** with source, license/access status, allowed use, and rationale.

## Binding playbooks and validators
- Read and follow `/home/argustest/research.md` before choosing the final thesis, benchmark, method name, metric, paper narrative, figure/table design, or final preflight.
- Use `/home/argustest/argus-skill` as the local source for built-in skills and validation commands.
- Prefer `/home/argustest/miniconda3/bin/python` for Argus validation commands.
- Final EMNLP completion requires this exact command to exit 0 and be quoted in completion evidence:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`
- `validate-pipeline`, a compiled PDF, a pilot run, or a passing review artifact alone is not final readiness.

## Operator goal
- Primary paper goal: [write the target research problem and deliverable]
- Target venue/scope: EMNLP/ACL long paper unless the operator explicitly says otherwise
- Success condition: final `validate-full-emnlp` exit 0 plus a current PDF/submission package
- Non-goals: [write what must not be optimized, copied, or claimed]
- Allowed compute/API budget: [write limits and stop conditions]

## Allowed starting inputs
List every starting input before using it:

| Input | Source/path/URL | License/access | How it may be used | Why it is appropriate |
| --- | --- | --- | --- | --- |
| [input] | [source] | [status] | [allowed use] | [rationale] |

If an input is not listed here, treat it as unavailable until documented. Raw evidence may be reused only as evidence; it does not carry over the old thesis, narrative, or figure design.

## Required project skeleton
Use the repository's existing conventions if they are already present; otherwise create the closest equivalent:

| Area | Required artifacts |
| --- | --- |
| Research | `research/RESEARCH_BRIEF.md`, `research/LITERATURE_REVIEW.md`, `research/LIT_MATRIX.tsv`, `research/LITERATURE_GROUNDING.json`, `research/IDEA_PROVENANCE.json`, `research/CODE_REUSE_PLAN.json`, `research/EXPERIMENT_PLAN.md` |
| Experiments | benchmark source/provenance files, run manifests, `status.json`, `progress.jsonl`, raw result JSON/TSV, logs, STOP-file contract |
| Paper | `paper/main.tex`, `paper/main.pdf`, verified BibTeX, `paper/PAGE_BUDGET.md`, `paper/TEMPLATE_SOURCE.md`, `paper/ARTIFACT_MANIFEST.json`, `paper/FORMAT_PREFLIGHT.md` |
| Style references | `paper/style_ref/exemplars/<slug>/paper.pdf`, extracted text, `paper/style_ref/EXEMPLAR.json`, `paper/style_ref/STYLE_PROFILE.md`, `paper/style_ref/SOURCES.md` |
| Reviews | `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/LAYOUT_REVIEW.json`, `paper/PAPER_QUALITY_CALIBRATION.json`, `paper/SUBMISSION_ASSURANCE.md`, `paper/SUBMISSION_ASSURANCE.json` |

## Role model
- Planner: decomposes the paper into gated research tasks and chooses the next blocker with the highest reviewer value.
- Engineer: implements benchmarks, experiments, generators, LaTeX, and fixes; edits source/generators rather than patching generated outputs.
- Reviewer: checks evidence, freshness, paper quality, and whether the completion command actually passed.
- Critic: challenges weak theses, duplicated benchmarks, validator gaming, ugly figures, stale artifacts, and overclaiming.
- Scientist: distills reusable lessons only after a task succeeds; write guidance for a smaller engineer model with concrete gates and anti-patterns.

## Research and idea provenance contract
1. Start from a research brief, not from a paper title.
2. Before selecting the final thesis, survey credible sources: recent high-quality papers, classic anchor papers, benchmark/dataset papers, official repos, and operator-specified trend sources when available.
3. Write `research/LITERATURE_GROUNDING.json` and require at least 10 recent high-quality papers plus at least 3 classic anchors unless access constraints are documented as blockers.
4. Generate candidate ideas only from literature, benchmark gaps, trend signals, and code/source discovery. Do not use free-form agent brainstorming as the source of novelty.
5. Write `research/IDEA_PROVENANCE.json` with `idea_generation_mode: literature_and_code_grounded` and `not_agent_brainstorm: true`.
6. Write `research/CODE_REUSE_PLAN.json`; prefer license-compatible official paper code, benchmark repos, and public libraries when appropriate instead of rewriting everything blindly.
7. Never copy paper or media prose. Store metadata, short paraphrased summaries, and original analysis.

## Benchmark and experiment contract
1. Final long-paper evidence must use unique semantic tasks/examples, not duplicated prompts, relabeling, suffixes, paraphrase inflation, or shuffled copies.
2. A small run is pilot evidence only. For final EMNLP readiness, target the full paper scale required by the local validators; if the validator requires at least 240 unique semantic scored main tasks/episodes, do not present 50--60 tasks as complete final evidence.
3. Use multiple independent benchmark/data sources when feasible. If synthetic tasks are used, document why, provide deterministic gold, task-family coverage, difficulty levels, leakage checks, and public or publicly releasable validation components.
4. For agent-skill/memory projects, include at least these baselines unless the operator documents a domain-specific replacement: `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method.
5. Include ablations, failure analysis, confidence intervals or statistical significance, and enough raw logs/results to reproduce every numerical claim.
6. Every long experiment must write `manifest.json`, `status.json`, `progress.jsonl`, logs, raw rows, and a STOP-file cancellation contract. `progress.jsonl` should expose current method, task count, total count, success/failure counts, last heartbeat, and latest artifact path so progress is visible while the daemon is running.

## Mandatory thick exemplar learning
1. Invoke the Paper Exemplar PDF Learning skill before drafting prose.
2. Download at least two open-access top-conference paper PDFs under `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. At least one exemplar should be a recent EMNLP/ACL best/outstanding/award paper when available; another should match the method/evaluation structure.
4. Extract text to `paper/style_ref/exemplars/<slug>/paper.txt`, compute and record `pdf_sha256`, record license and `pdf_storage_policy`, and write `paper/style_ref/SOURCES.md`.
5. `paper/style_ref/EXEMPLAR.json` must use `exemplar_schema_version: 2` and include `local_pdf`, `text_extract`, `pdf_sha256`, `license`, `pdf_storage_policy`, `usage: "structural_style_only"`, and `no_prose_copy: true` for every exemplar.
6. Write a thick `paper/style_ref/STYLE_PROFILE.md` covering abstract shape, section/page allocation, figure/table inventory, related-work shape, evaluation layout, formatting/layout lessons, writing lessons, transfer plan, and no-prose-copy policy.
7. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`; URL-only exemplars are blockers.
8. Use exemplars only for structural style learning. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

## Paper narrative and prose contract
1. Do not write the abstract first. Draft the abstract after the main numbers, ablations, and limitations exist.
2. The paper must have one sentence-long contribution: "We propose X. We show X improves Y by Z because W." If X, Y, Z, and W cannot be filled from evidence, the paper is not ready.
3. The abstract should read like a normal EMNLP abstract: problem, gap, method, result, implication. Do not expose validator names, raw paths, evidence-span bookkeeping, review mechanics, or appendix layout trivia in the abstract/body.
4. Every numerical paper claim must trace to raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
5. Keep claims calibrated without turning the paper into repetitive defensive caveats. Move detailed scope limits to limitations/discussion.
6. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.

## Paper formatting and layout contract
1. Use the official ACL/EMNLP style files, preferably `\usepackage[review]{acl}`, and the anonymous review author block `Anonymous EMNLP Submission`.
2. The final paper must be an EMNLP/ACL long paper with 7.5--8.0 pages of main content, verified citations, limitations, ethics, and a reproducibility appendix.
3. Conclusion must appear by the end of page 8; Limitations and Ethical Considerations must appear after Conclusion; References must appear before Appendix.
4. Run `validate-research-md-format` after final compile and before academic-language/layout review.
5. Write `paper/FORMAT_PREFLIGHT.md` with compile command/status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
6. No undefined references/citation warnings, no rendered `[?]`, no `Overfull \hbox > 5pt`, no placeholders/TODO/TBD/FIXME, no `% UNVERIFIED`, and no ugly code-like display labels in title, abstract, headings, captions, figures, or tables.
7. Body figures <=5 total, at most one `figure*`, every figure labeled and referenced, every table caption has a numerical headline, at least one figure/table on each of pages 4--7 when extractable, and at least one paired-significance table when comparative binary outcomes apply.
8. Tables must follow the `research.md` style tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent only for meaningful degradation, and bold winning values.

## Figure contract
1. Use image-2/codex-image2 for at least one core conceptual figure.
2. Figure 1, teaser, overall, and core method/framework/system/pipeline overview figures must include the actual generated image-2 raster `output_path` directly from `paper/main.tex`.
3. Preserve prompt, metadata, generation provenance, inspect/review artifacts, SHA-256, width, and height. Do not crop, downsample, resave, overwrite, redraw, trace, vectorize, PDF-wrap, screenshot, or relabel the image after provenance is written.
4. Do not replace the overview with matplotlib/FancyBboxPatch, TikZ node graphs, PIL/SVG/HTML canvases, manual vector tools, cleaned PDF derivatives, or locally drawn mockups. If it is ugly, regenerate through image-2 with a better prompt.
5. Conceptual figures must be adaptive or landscape page-width assets, preferably `1536x1024` or `1920x1080`; do not use square `1024x1024`, weird/sketchy fonts, tiny text, heavy gradients, photorealism, excessive logos, or decorative clutter.
6. Data figures and tables must be generated from local raw data/results, not from image-2.

## Final review and assurance
1. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write`, then `validate-academic-language-review`. The score must be at least 4/5, model-backed, fresh, with quoted evidence spans, `needs_revision: false`, and no active directives.
2. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write`, then `validate-layout-review`. The score must be at least 4/5, vision-backed from rendered PDF page snapshots, fresh, and `needs_revision: false`.
3. Write `paper/SUBMISSION_ASSURANCE.md` and `paper/SUBMISSION_ASSURANCE.json`.
4. Review artifacts, calibration files, and readiness reports are evidence, not optimization targets. Never hand-edit them to say PASS/ready while underlying validators fail.
5. Never emit PASS/WARN/final-ready if any required validator fails. `WARN` cannot launder pilot scale, stale reviews, missing provenance, or known hard blockers into final EMNLP readiness.

## Operational safety
1. Work inside this project directory unless reading `/home/argustest/research.md` or `/home/argustest/argus-skill`.
2. Never copy `/home/argustest`, `.skill-agent`, `.argus-skill`, `.cache`, model caches, or recursive workspaces into this project.
3. Keep API keys and capability vault contents out of all artifacts.
4. Record meaningful decisions and evidence in project files, not only in chat.
5. Preserve user edits and unrelated work. Do not revert files you did not intentionally change.

## Forbidden shortcuts
- Do not fake experiments, citations, provenance, tests, reviews, image-2 artifacts, or validation outputs.
- Do not edit generated paper artifacts without updating the generator/source and manifest.
- Do not satisfy validators by adding boilerplate that makes the actual paper worse.
- Do not copy a previous project and rename variables to make it look new.
- Do not silently ignore failed commands, missing artifacts, stale reviews, or validator blockers.

## Completion contract
A task is complete only when:
- the requested paper artifact or blocker fix exists in source and regenerated artifacts,
- source, generated artifacts, manifests, reviews, and validation reports are synchronized,
- relevant validation has passed or remaining failures are explicitly unrelated to the task,
- known limitations are documented without pretending they are solved,
- the handoff states what changed, what passed, and the next highest-priority blocker.

The full project is complete only when the final `validate-full-emnlp` command above exits 0 on the current workspace and that exact output is quoted in completion evidence.
```

## Generality check
This template is EMNLP/ACL-paper-specific but must stay project-neutral. It may contain stable local Argus paths and validation commands, but it must not contain a specific project title, benchmark name, result number, figure name, or prior-workspace story.

## Coverage check
Before using the template, fill all bracketed placeholders, list allowed inputs, and delete no hard gate unless the operator explicitly changes the paper scope.
