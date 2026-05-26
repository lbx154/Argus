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
- Use `/home/argustest/argus-skill` as the local Argus source tree. Built-in skill markdown lives at `/home/argustest/argus-skill/argus_skill/builtin_skills/` and as the Python package resource `argus_skill.builtin_skills`.
- If this workspace does not already have local copies, export the built-in skills so the daemon can read them directly:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --export-builtin-skills ./argus_builtin_skills`
- Read `./argus_builtin_skills/*.md` first when invoking built-in paper/research skills. If the local copy is absent or stale, fall back to `/home/argustest/argus-skill/argus_skill/builtin_skills/`. Do not copy the whole Argus repository, global memory, model caches, or capability vault into this project.
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

## Model/API and helper-code repair contract
1. Model and image credentials are operator capabilities, not project artifacts. The private vault is `~/.argus-skill/capabilities/model_api.json` or `ARGUS_SKILL_CAPABILITY_VAULT`; it should be mode `0600`. Do not manually open/read, print, summarize, copy, or commit its raw contents; only Argus route helpers/tools may load it at runtime.
2. Before model-backed repair or review work, run the secret-free status check:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --model-api-status`
   Use the reported routes: `scientist` for literature/claim synthesis, `engineer` for code/evaluation helpers, `reviewer` for audits, `image` for image-2/codex-image2 generation, and `image_review` for visual inspection. If a needed route is unavailable but operator-approved environment/Codex config exists, initialize once with:
   `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill --init-model-api`
3. Keep or create reusable wrappers under `code/`; do not scatter raw API calls through paper generators or review JSON writers. Use `load_model_api_route(...)` from Argus, not hard-coded keys, base URLs, or model names. Route-specific environment overrides such as `ARGUS_SKILL_IMAGE_MODEL=gpt-image-2`, `ARGUS_SKILL_IMAGE_BASE_URL`, and `ARGUS_SKILL_IMAGE_API_KEY` may be used only as process environment, never as committed text.
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

5. For image-2 Figure 1 repair, prefer the Argus image tool and preserve the exact raster it returns:

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

   A helper such as `code/generate_image2_figure.py` must then write or refresh `paper/figures/IMAGE2_FIGURES.json` with `figure_id`, `figure_type`, `model` or `generator_model`, `prompt_path`, `output_path`, `output_sha256`, `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, width, and height. `generation_provenance_path` may point at the image sidecar if that JSON records `prompt_path`, `output_path`, and `output_sha256`. Never crop, downsample, resave, PDF-wrap, or locally redraw the accepted raster after this provenance is written.
6. If the current Figure 1/teaser is ugly, cramped, misspelled, square, generic, or prompt-thin, do not patch it with matplotlib/TikZ/PDF/vector redraws. Regenerate through image-2 from this scaffold, generating 6--20 layout variants by changing only the `Layout variant` block; keep the best reviewed raster and record the selected `prompt_variant_id` in provenance or the manifest:

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

## Citation and related-work repair
1. Verify bibliography metadata through Semantic Scholar, arXiv, CrossRef, ACL Anthology, DBLP, or official project pages; never invent BibTeX to clear a warning.
2. Keep references separated by claim/topic/section. Each related-work paragraph must cite the papers it actually discusses; do not concentrate all citations in one giant paragraph, one mega-sentence, a caption, or a detached bibliography block.
3. Maintain or repair a literature matrix with topic, paper key, verified source, claim supported, and intended paper section before editing related work.
4. Starter targets for memory, agent-skill, and hallucination papers are retrieval targets only:
   - Tool-use and agent loops: `yao2023react`, `shinn2023reflexion`, `madaan2023selfrefine`, `schick2023toolformer`, `qin2023toolllm`, `li2023apibank`, `patil2023gorilla`, `shen2023hugginggpt`, `karpas2022mrkl`.
   - Memory, skills, and long-horizon agents: `wang2024voyager`, `zhao2024expel`, `packer2023memgpt`, `park2023generativeagents`, `xu2025amem`, `zhong2024memorybank`, `wang2023longmem`.
   - Self-evolution and process supervision: `qi2024webrl`, `li2025webevolver`, `wang2025mobileagente`, `tang2025sage`, `zhang2025skillrl`, `lightman2023letsverify`, `zelikman2022star`.
   - Evaluation, hallucination, and multi-agent surveys: `zheng2023judging`, `ji2023survey`, `huang2025hallucination`, `guo2024llmmas`, `manakul2023selfcheckgpt`, `lin2022truthfulqa`.
   - Agent benchmarks and validation environments: `liu2023agentbench`, `zhou2023webarena`, `mialon2023gaia`, `maharana2024locomo`, `shridhar2020alfworld`.
5. Add domain-specific EMNLP/ACL papers, benchmark papers, dataset papers, and official repos from the literature survey until the final paper clears the bibliography-depth gate. Unrelated domains need their own topic-specific starter list.

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
4. Refresh `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` before prose repair. It must map exemplar lessons to the current paper's section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, and local evidence mapping.
5. Rebuild `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual final `paper/main.tex` section order after repair. Use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every top-level section before References/Appendix.
6. The repair target is not to preserve messy filler. Remove or merge unmapped sections such as `Protocol Notes`, `Track Mechanics`, `Release Detail`, `Mechanics`, or `Notes`; if a nonstandard paper-specific section is genuinely necessary, map it with `maps_to_exemplar_phase`, cite local `evidence_sources`, attach an `exemplar_lesson`, and write a `deviation_rationale`.
7. Run `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`; URL-only exemplars and missing structure blueprints remain blockers. Final readiness additionally checks `STRUCTURE_CONFORMANCE`.
8. Use exemplars only for structure. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

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
