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
- Use `/home/argustest/argus-skill` as the local Argus source tree. Built-in skill markdown lives at `/home/argustest/argus-skill/argus_skill/builtin_skills/` and as the Python package resource `argus_skill.builtin_skills`.
- At project setup, copy the built-in skill markdown into this workspace so the daemon can read it directly:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --export-builtin-skills ./argus_builtin_skills`
- Read `./argus_builtin_skills/*.md` first when invoking built-in paper/research skills. If the local copy is absent or stale, fall back to `/home/argustest/argus-skill/argus_skill/builtin_skills/`. Do not copy the whole Argus repository, global memory, model caches, or capability vault into this project.
- Prefer `/home/argustest/miniconda3/bin/python` for Argus validation commands.
- Final EMNLP completion requires this exact command to exit 0 and be quoted in completion evidence:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`
- Full-scale experiment evidence is a prerequisite for analysis, narrative, drafting, assurance, and submission. This command must pass before any of those stages are marked ready/done:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .`
- Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
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
| Style references | `paper/style_ref/exemplars/<slug>/paper.pdf`, extracted text, `paper/style_ref/EXEMPLAR.json`, `paper/style_ref/STYLE_PROFILE.md`, `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.json`, `paper/style_ref/SOURCES.md` |
| Local Argus skills | `argus_builtin_skills/*.md` exported from `/home/argustest/argus-skill/argus_skill/builtin_skills/` |
| Reviews | `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/LAYOUT_REVIEW.json`, `paper/PAPER_QUALITY_CALIBRATION.json`, `paper/SUBMISSION_ASSURANCE.md`, `paper/SUBMISSION_ASSURANCE.json` |

## Model/API and helper-code contract
1. Model and image credentials are operator capabilities, not project artifacts. The private vault is `~/.argus-skill/capabilities/model_api.json` or `ARGUS_SKILL_CAPABILITY_VAULT`; it should be mode `0600`. Do not manually open/read, print, summarize, copy, or commit its raw contents; only Argus route helpers/tools may load it at runtime.
2. Before model-backed work, run the secret-free status check:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --model-api-status`
   Use the reported routes: `scientist` for literature/idea synthesis, `engineer` for code/evaluation helpers, `reviewer` for audits, `image` for image-2/codex-image2 generation, and `image_review` for visual inspection. If a needed route is unavailable but operator-approved environment/Codex config exists, initialize once with:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --init-model-api`
3. Put reusable project wrappers under `code/`; do not scatter raw API calls through notebooks, paper generators, or review JSON writers. Use `load_model_api_route(...)` from Argus, not hard-coded keys, base URLs, or model names. Route-specific environment overrides such as `ARGUS_SKILL_IMAGE_MODEL=gpt-image-2`, `ARGUS_SKILL_IMAGE_BASE_URL`, and `ARGUS_SKILL_IMAGE_API_KEY` may be used only as process environment, never as committed text.
4. Minimal `code/llm.py` pattern for text calls:

       from __future__ import annotations

       import json
       import urllib.request
       from typing import Any

       from argus_skill.tools.capability_vault import ModelApiRoute, load_model_api_route

       def _route(name: str) -> ModelApiRoute:
           route = load_model_api_route(name)
           if route is None or not route.usable:
               raise RuntimeError(f"model API route {name!r} is unavailable; run --model-api-status")
           return route

       def _post(route: ModelApiRoute, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
           req = urllib.request.Request(
               f"{route.base_url.rstrip('/')}/{endpoint.lstrip('/')}",
               data=json.dumps(payload).encode("utf-8"),
               headers={"Authorization": f"Bearer {route.api_key}", "Content-Type": "application/json"},
               method="POST",
           )
           with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 - Argus capability route
               return json.loads(resp.read().decode("utf-8"))

       def complete(prompt: str, *, route_name: str = "scientist", system: str = "") -> str:
           route = _route(route_name)
           if route.wire_api == "chat":
               data = _post(route, "/chat/completions", {
                   "model": route.model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
               })
               return data["choices"][0]["message"]["content"].strip()
           data = _post(route, "/responses", {
               "model": route.model,
               "input": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
           })
           if isinstance(data.get("output_text"), str):
               return data["output_text"].strip()
           return "\n".join(
               part.get("text", "").strip()
               for item in data.get("output", [])
               for part in item.get("content", [])
               if isinstance(part, dict) and part.get("text")
           )

5. For image-2 Figure 1 generation, prefer the Argus image tool and preserve the exact raster it returns:

       PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.tools.image_tool generate \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.png \
         --size 1536x1024 --force
       PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.tools.image_tool inspect \
         --image paper/figures/method_overview.png > paper/figures/method_overview.inspect.json
       PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.tools.image_tool review \
         --image paper/figures/method_overview.png \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.review.json

   A helper such as `code/generate_image2_figure.py` must then write `paper/figures/IMAGE2_FIGURES.json` with `figure_id`, `figure_type`, `model` or `generator_model`, `prompt_path`, `output_path`, `output_sha256`, `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, width, and height. `generation_provenance_path` may point at the image sidecar if that JSON records `prompt_path`, `output_path`, and `output_sha256`. Never crop, downsample, resave, PDF-wrap, or locally redraw the accepted raster after this provenance is written.
6. Do not let the model freehand a one-paragraph image prompt. Before calling image-2, write `paper/figures/method_overview.prompt.txt` from this teaser scaffold, then generate 6--20 layout variants by changing only the `Layout variant` block; keep the best reviewed raster and record the selected `prompt_variant_id` in provenance or the manifest:

       Use case: scientific-educational
       Asset type: Figure 1 teaser / conceptual overview for an EMNLP/ACL academic manuscript

       General style:
       - EMNLP/ACL paper method figure, full-width page-width landscape, 1536x1024 or 1920x1080.
       - Clean Figma-style block diagram: rounded cards, neat alignment, soft pastel fills, thin dark-gray borders, compact information density.
       - Polished manuscript figure, not a dashboard, poster, screenshot, marketing graphic, or whiteboard sketch.
       - Large readable labels, short phrases, balanced hierarchy, no snake_case identifiers in visible text.
       - Flat vector-like raster rendering on a warm white background (#fbfaf7).

       Pinned content that must appear exactly:
       - Title: "<short human-readable method/system name>"
       - Stage labels: "<input/source>", "<core mechanism>", "<verification/gating step>", "<output/result>".
       - Outcome chips: "<main benefit>", "<main evidence object>", "<failure avoided>".
       - SPELL EXACTLY the quoted labels above; do not invent extra terminology.

       Layout variant: choose one and name it, e.g. horizontal swimlanes, central hero composition, sankey funnel, exploded-view, layered architecture stack, pipeline plus gallery, hub-and-spoke, four-panel A/B/C/D, or polished Figma wireframe.

       Negative prompt / Avoid:
       - no tiny unreadable text, no paragraphs, no code snippets, no raw paths, no watermark
       - no photorealism, no heavy gradients, no glassmorphism, no logo wall
       - no messy Excalidraw look, no arbitrary blobs, no decorative clutter
       - no inconsistent terminology between figure and paper

   A prompt that lacks `General style`, `Pinned content`, exact spelling instructions, `Layout variant`, and `Negative prompt / Avoid` is a blocker even if the image API call succeeds.

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
3. Benchmark construction is not execution. `benchmarks/full/tasks.jsonl`, benchmark manifests, or `status.json task_count` do not satisfy final evidence unless raw completed scored rows under `experiments/**` cover every required method/baseline condition.
4. Use multiple independent benchmark/data sources when feasible. If synthetic tasks are used, document why, provide deterministic gold, task-family coverage, difficulty levels, leakage checks, and public or publicly releasable validation components.
5. For agent-skill/memory projects, include at least these baselines unless the operator documents a domain-specific replacement: `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method. Each required condition must have at least 240 distinct scored main tasks/episodes for final-paper claims.
6. Include ablations, failure analysis, confidence intervals or statistical significance, and enough raw logs/results to reproduce every numerical claim.
7. Every long experiment must write `manifest.json`, `status.json`, `progress.jsonl`, logs, raw rows, and a STOP-file cancellation contract. `progress.jsonl` should expose current method, task count, total count, success/failure counts, last heartbeat, and latest artifact path so progress is visible while the daemon is running.
8. Run `validate-full-scale-evidence` before analysis/drafting; if it fails, write only pilot diagnostics and queue the missing full-run/matrix-completion work.

## Mandatory thick exemplar learning
1. Invoke the Paper Exemplar PDF Learning skill before drafting prose.
2. Download at least two open-access top-conference paper PDFs under `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. At least one exemplar should be a recent EMNLP/ACL best/outstanding/award paper when available; another should match the method/evaluation structure.
4. Extract text to `paper/style_ref/exemplars/<slug>/paper.txt`, compute and record `pdf_sha256`, record license and `pdf_storage_policy`, and write `paper/style_ref/SOURCES.md`.
5. `paper/style_ref/EXEMPLAR.json` must use `exemplar_schema_version: 2` and include `local_pdf`, `text_extract`, `pdf_sha256`, `license`, `pdf_storage_policy`, `usage: "structural_style_only"`, and `no_prose_copy: true` for every exemplar.
6. Write a thick `paper/style_ref/STYLE_PROFILE.md` covering abstract shape, section/page allocation, figure/table inventory, related-work shape, evaluation layout, formatting/layout lessons, writing lessons, transfer plan, and no-prose-copy policy.
7. Write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` before prose. It must map exemplar lessons to this paper's section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, and local evidence mapping.
8. After drafting, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual `paper/main.tex` section order. The JSON must use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every final top-level section before References/Appendix.
9. Every section mapping must include `maps_to_exemplar_phase`, `evidence_sources`, `exemplar_lesson`, and a paper-specific `deviation_rationale` for nonstandard sections. The paper may adapt exemplar architecture to the current thesis, but unmapped/freehand filler sections such as `Protocol Notes`, `Track Mechanics`, `Release Detail`, `Mechanics`, or `Notes` are blockers.
10. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`; URL-only exemplars and missing structure blueprints are blockers. Final readiness additionally checks `STRUCTURE_CONFORMANCE`.
11. Use exemplars only for structural style learning. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

## Paper narrative and prose contract
1. Do not write the abstract first. Draft the abstract after the main numbers, ablations, and limitations exist.
2. The paper must have one sentence-long contribution: "We propose X. We show X improves Y by Z because W." If X, Y, Z, and W cannot be filled from evidence, the paper is not ready.
3. The abstract should read like a normal EMNLP abstract: problem, gap, method, result, implication. Do not expose validator names, raw paths, evidence-span bookkeeping, review mechanics, or appendix layout trivia in the abstract/body.
4. Every numerical paper claim must trace to raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
5. Keep claims calibrated without turning the paper into repetitive defensive caveats. Move detailed scope limits to limitations/discussion.
6. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.

## Citation and related-work contract
1. Use starter citation targets only when the topic matches. Treat keys as retrieval targets, not as ready BibTeX: verify each entry through Semantic Scholar, arXiv, CrossRef, ACL Anthology, DBLP, or official project pages.
2. Keep references separated by claim/topic/section. Each related-work paragraph must cite the specific papers it discusses; do not dump all citations into one dense paragraph, one mega-sentence, a caption, or the bibliography with no local discussion.
3. Maintain a literature matrix with topic, paper key, verified source, claim supported, and intended paper section before drafting related work.
4. Starter targets for memory, agent-skill, and hallucination papers:
   - Tool-use and agent loops: `yao2023react`, `shinn2023reflexion`, `madaan2023selfrefine`, `schick2023toolformer`, `qin2023toolllm`, `li2023apibank`, `patil2023gorilla`, `shen2023hugginggpt`, `karpas2022mrkl`.
   - Memory, skills, and long-horizon agents: `wang2024voyager`, `zhao2024expel`, `packer2023memgpt`, `park2023generativeagents`, `xu2025amem`, `zhong2024memorybank`, `wang2023longmem`.
   - Self-evolution and process supervision: `qi2024webrl`, `li2025webevolver`, `wang2025mobileagente`, `tang2025sage`, `zhang2025skillrl`, `lightman2023letsverify`, `zelikman2022star`.
   - Evaluation, hallucination, and multi-agent surveys: `zheng2023judging`, `ji2023survey`, `huang2025hallucination`, `guo2024llmmas`, `manakul2023selfcheckgpt`, `lin2022truthfulqa`.
   - Agent benchmarks and validation environments: `liu2023agentbench`, `zhou2023webarena`, `mialon2023gaia`, `maharana2024locomo`, `shridhar2020alfworld`.
5. Add domain-specific EMNLP/ACL papers, benchmark papers, dataset papers, and official repos from the literature survey until the final paper clears the bibliography-depth gate. Unrelated domains need their own topic-specific starter list.

## Paper formatting and layout contract
1. Use the official ACL/EMNLP style files, preferably `\usepackage[review]{acl}`, and the anonymous review author block `Anonymous EMNLP Submission`.
2. The final paper must be an EMNLP/ACL long paper with 7.5--8.0 pages of main content, verified citations, limitations, ethics, and a reproducibility appendix.
3. Use this reference page budget when writing `paper/PAGE_BUDGET.md` and `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`; adapt only with evidence/exemplar justification:

   | Section | Pages |
   | --- | --- |
   | Abstract | 0.3 |
   | Introduction | 1 |
   | Related Work | 0.5--0.8 |
   | Method | 1--1.5 |
   | Experimental Setup | 0.5--1 |
   | Main Results | 1--1.5 |
   | Analysis/Ablation | 1 |
   | Failure Cases | 0.3--0.5 |
   | Conclusion | 0.2 |

4. Conclusion must appear by the end of page 8; Limitations and Ethical Considerations must appear after Conclusion; References must appear before Appendix.
5. Run `validate-research-md-format` after final compile and before academic-language/layout review.
6. Write `paper/FORMAT_PREFLIGHT.md` with compile command/status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
7. No undefined references/citation warnings, no rendered `[?]`, no `Overfull \hbox > 5pt`, no placeholders/TODO/TBD/FIXME, no `% UNVERIFIED`, and no ugly code-like display labels in title, abstract, headings, captions, figures, or tables.
8. Body figures <=5 total, at most one `figure*`, every figure labeled and referenced, every table caption has a numerical headline, at least one figure/table on each of pages 4--7 when extractable, and at least one paired-significance table when comparative binary outcomes apply.
9. Tables must follow the `research.md` style tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent only for meaningful degradation, and bold winning values.

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
