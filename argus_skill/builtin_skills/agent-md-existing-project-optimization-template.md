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
- Read `./argus_builtin_skills/*.md` and `./argus_builtin_skills/**/*.md` first when invoking built-in paper/research/domain skills. If the local copy is absent or stale, fall back to `/home/argustest/argus-skill/argus_skill/builtin_skills/`. Do not copy the whole Argus repository, global memory, model caches, or capability vault into this project.
- When ownership is unclear, read `./argus_builtin_skills/emnlp-paper-skill-router.md` first, then load the specific skill it routes to.
- Prefer `/home/argustest/miniconda3/bin/python` for Argus validation commands.
- Final EMNLP completion requires this exact command to exit 0 and be quoted in completion evidence:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`
- Full-scale experiment evidence is a prerequisite for analysis, narrative, drafting, assurance, and submission. This command must pass before any of those stages are marked ready/done:
  `PYTHONPATH=/home/argustest/argus-skill /home/argustest/miniconda3/bin/python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .`
- Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
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
4. Officially launched projects include `code/llm.py`; prefer repairing that helper over
   scattering raw HTTP calls through generators or experiment code. If you must edit or
   replace it, preserve transient 429/5xx/URL retry with exponential backoff and
   `Retry-After` handling. Do not convert a rate-limit, disconnect, or temporary backend
   error directly into a deterministic fallback answer for an experiment row; retry first,
   then record the failure explicitly if the route is still unusable. Minimal `code/llm.py`
   pattern for text calls:

       from __future__ import annotations

       import json
       import time
       import urllib.error
       import urllib.request
       from typing import Any

       from argus_skill.tools.capability_vault import ModelApiRoute, load_model_api_route

       TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}

       def _route(name: str) -> ModelApiRoute:
           route = load_model_api_route(name)
           if route is None or not route.usable:
               raise RuntimeError(f"model API route {name!r} is unavailable; run --model-api-status")
           return route

       def _retry_delay_seconds(exc: BaseException, attempt: int) -> float | None:
           if isinstance(exc, urllib.error.HTTPError):
               if exc.code not in TRANSIENT_HTTP_STATUS_CODES:
                   return None
               retry_after = exc.headers.get("Retry-After") if exc.headers else None
               if retry_after:
                   try:
                       return max(1.0, float(retry_after))
                   except ValueError:
                       pass
           elif not isinstance(exc, urllib.error.URLError):
               return None
           return min(60.0, 2.0 * (2**attempt))

       def _post(route: ModelApiRoute, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
           req = urllib.request.Request(
               f"{route.base_url.rstrip('/')}/{endpoint.lstrip('/')}",
               data=json.dumps(payload).encode("utf-8"),
               headers={"Authorization": f"Bearer {route.api_key}", "Content-Type": "application/json"},
               method="POST",
           )
           for attempt in range(5):
               try:
                   with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 - Argus capability route
                       return json.loads(resp.read().decode("utf-8"))
               except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                   delay = _retry_delay_seconds(exc, attempt)
                   if delay is None or attempt == 4:
                       raise
                   time.sleep(delay)
           raise RuntimeError("unreachable")

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

   A helper such as `code/generate_image2_figure.py` must then write or refresh `paper/figures/IMAGE2_FIGURES.json` with `figure_id`, `figure_type`, `model` or `generator_model`, `prompt_path`, `output_path`, `output_sha256`, `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, width, and height. The sidecar must preserve image-tool/API evidence (`/images/generations`, model, created time, prompt SHA, output SHA, dimensions), and `review_path` must come from the `image_review` model route. `generation_provenance_path` may point at the image sidecar if that JSON records `prompt_path`, `output_path`, and `output_sha256`. Never crop, downsample, resave, PDF-wrap, locally redraw the accepted raster, or hand-fill `codex-image2` metadata around a local PNG after this provenance is written.
6. If the current Figure 1/teaser is ugly, cramped, misspelled, square, generic, or prompt-thin, do not patch it with matplotlib/TikZ/PDF/vector redraws. Regenerate through image-2 from this scaffold, generating 6--20 layout variants by changing only the `Layout variant` block; keep the best reviewed raster and record the selected `prompt_variant_id` in provenance or the manifest:

       Use case: scientific-educational
       Asset type: Figure 1 teaser / conceptual overview for an EMNLP/ACL/NeurIPS-style academic manuscript.

       General style:
       - EMNLP/ACL/NeurIPS/CS paper method figure, full-width two-column landscape, 1536x1024 or 1920x1080.
       - Clean Figma-style block diagram / block-based Figma style with rounded cards, neat alignment, soft pastel fills, dark-gray 2px borders, and compact information density.
       - Compact, information-rich, suitable for a PDF page-width figure; little wasted space but not crowded.
       - Tidy rounded handwritten or friendly sans-serif feel is acceptable only if it remains crisp and readable; no messy sketch fonts.
       - Moderate badge/icon use only when semantically useful; a few simple recognizable icons are fine, not a logo wall.
       - No heavy shadows, no gradients, no photorealism, no glassmorphism, no messy Excalidraw look.
       - Large readable labels, short phrases, balanced hierarchy, flat vector-like raster rendering on warm white #fbfaf7.

       Style intent:
       - Clean, dense, modular, Figma-like, mostly rounded cards, low-saturation pastel blocks.
       - Use small badges/icons sparingly; avoid empty space while preserving alignment.
       - It should look like a main figure in an EMNLP/ACL/NeurIPS paper, not a marketing graphic, stock illustration, dashboard screenshot, or casual whiteboard.

       Pinned content that must appear exactly:
       - Title: "<short human-readable method/system name>"
       - Show: "<source/input>" -> "<parse/build/distill step>" -> "<quality/verification gate>" -> "<memory/library/model state>" -> "<agent/execution step>" -> "<output/result>" -> "<benchmark/evidence protocol>".
       - Components/chips: "<baseline/status quo>", "<proposed method>", "<accepted item>", "<rejected item>", "<main metric/evidence>", "<failure avoided>".
       - SPELL EXACTLY every quoted label above. Do not invent alternate terminology, code identifiers, raw artifact paths, or extra labels.

       Layout variant:
       - Pick one variant ID and name it in the prompt. Swap only this block when generating variants.
       - 01 central hero: huge central memory/wiki/library card, source factory on the left, agent/output board on the right, benchmark strip at bottom.
       - 02 horizontal swimlanes: three clean lanes such as Build, Verify, Execute; use offset cards so it is not too rigid.
       - 03 sankey funnel: many sources merge into distillation, narrow through gates, expand into library/state, then branch to outputs.
       - 04 exploded entry: one accepted skill/memory/wiki entry pulled apart into Text, Visual, Recipe, Metadata plates with callout arrows.
       - 05 layered architecture stack: bottom sources, middle reusable memory/library, top agent execution; use shelf-like overlapping slabs.
       - 06 pipeline plus gallery: main pipeline across top, output gallery on right, compact benchmark/evidence cards along bottom.
       - 07 modular dashboard: dense but paper-clean cards; central method card largest, side panel for domains/tasks/outputs.
       - 08 radial hub-spoke: reusable library/state as center hub; sources feed from left arc; agent/results radiate right; evidence panel below.
       - 09 zigzag pipeline: Z-shaped reading path with numbered step badges and compact insets.
       - 10 research-poster dense: section headers, compact cards, mini charts, and small output thumbnails; still clean Figma and paper-friendly.
       - 11 grayscale accent: mostly grayscale academic style with two pastel accent colors for proposed path and verification.
       - 12 color-coded phases: peach acquisition, blue memory/library, green agent, lavender domains, yellow benchmark; overlapping phase tabs.
       - 13 card deck: sources, skills, and outputs as tidy fanned decks; one accepted card expanded.
       - 14 computation graph: nodes and grouped modules with thin arrows and rounded containers, like an ML systems diagram.
       - 15 dataflow with sidebars: main flow through center, left source sidebar, right output sidebar, bottom benchmark/evidence sidebar.
       - 16 timeline plus insets: left-to-right timeline with zoom boxes for the core mechanism and output/evidence.
       - 17 nested containers: big containers for Offline Construction and Online Execution; nested subcards plus benchmark footer.
       - 18 multi-panel A/B/C/D: A sources/build, B reusable state, C agent execution, D benchmark/evidence; panels overlap slightly and share arrows.
       - 19 light blueprint: pale blue grid background, modular boxes, thin connector routes, neat badges, strong central method box.
       - 20 polished Figma wireframe: component frames, auto-layout-like spacing, section tabs, chips, and carefully staggered components.

       Negative prompt / Avoid:
       - no concrete code snippets, raw paths, tiny unreadable text, character-level vertical text, or dense paragraphs
       - no excessive logos or brand marks, no watermark
       - no photorealistic scenes, stock photos, glassmorphism, heavy gradients, heavy shadows, texture, or arbitrary decorative blobs
       - no messy whiteboard / Excalidraw-heavy sketch style
       - no large empty areas, overlapping cards, squashed labels, inconsistent terminology, or extra captions that make it look like a dashboard

       Figma tokens for camera-ready cleanup:
       - Canvas 1536x1024 or 1920x1080; background #fbfaf7; stroke #1f2933 at 2px.
       - Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
       - Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
       - Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.

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
10. If the same `pytest` test, validator issue code, or review-span lookup fails twice in a row, enter repeated-failure mode. Before another edit, capture the full traceback/assertion, expected value, actual value, and the exact fixture/artifact path. Do not keep guessing fallback terms. Decide whether the authoritative fix belongs in source/generator code, raw artifact regeneration, or a synthetic test fixture; then make one narrow fix and rerun the failing command.

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
3. A small single-source run is pilot evidence even if all methods ran successfully; final long-paper evidence requires an executed multi-source matrix, not a lone large slice.
4. Benchmark construction is not execution. `benchmarks/full/tasks.jsonl`, benchmark manifests, or `status.json task_count` do not satisfy final evidence unless raw completed scored rows under `experiments/**` cover every required method/baseline condition.
5. Benchmark scale must come from unique semantic tasks/examples, not duplicates, relabeling, suffixes, paraphrase inflation, or shuffled copies.
6. For agent-skill/memory projects, each required baseline/method condition such as `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method must be evaluated on the same executed multi-source benchmark matrix, unless the operator documents a domain-specific replacement.
7. Final paper evidence needs at least 3 independent executed real benchmark/data sources or official task-release families. Same-family slices do not count as separate sources. Planned diagnostic rows, future slices, and manifests without raw scored rows do not count.
8. Benchmark/source selection must document source diversity, recency/relevance, adoption/rejection decisions, license/access status, leakage controls, and why each source tests a distinct capability.
9. If no local GPU is configured, use the approved hosted LLM route for runnable agent experiments instead of a toy oracle/policy; `gpt-5-mini` is the default low-cost no-GPU backbone unless the operator specifies another model. Record model id, endpoint/provider class, temperature, top_p, max_tokens, token/request budget, cache/retry/timeout policy, and stopping rules in internal manifests. In the paper, report only evaluated-system facts such as model/backend, benchmark, metric, budget/decoding, and high-level cost; never expose local device/cache/path or Argus/Codex route configuration.
10. Every numeric claim must remain tied to current raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
11. Run `validate-full-scale-evidence` before final analysis/drafting/assurance repair. If it fails, preserve valid raw evidence but queue the missing full-run or matrix-completion work and keep the PDF non-final.
12. If the method-positive thesis is rejected by evidence, queue repair/pivot tasks for method, metric, benchmark, or objective. Do not convert it into a negative-result paper unless the operator explicitly asks.

## Existing paper repair
1. Improve the artifact the reader/reviewer sees, not just the validator surface. Reader-facing prose must stay clear, specific, and evidence-backed. Readability includes basic reproducibility facts about the **evaluated paper system**, not the Argus/Codex machinery that wrote the paper: the main body must tell an outside reviewer what agent framework/runtime or benchmark harness was used, which evaluated model/backend powered the agent runs, what controller/skill/memory mechanism changed behavior, which benchmark/tasks/baselines/metrics/budgets were used, and how one episode executes. LLM/model identifiers must be visible whenever the evaluated agent calls a model. For no-GPU final agent experiments, report the evaluated hosted model such as `gpt-5-mini`; if the benchmark is deterministic/no-external-model, downgrade it to a deterministic baseline or pilot instead of presenting it as final agent-system evidence. Put this in Method/Experimental Setup prose or a compact table; never expose API keys, private vault contents, local device/cache/path details, or Argus/Codex authoring route names.
2. If academic-language review repeatedly says the headline mechanism is unsupported or not isolated, reset the claim instead of polishing the same sentence again. Use one exact end-to-end result as the paper's headline: method/system name, comparator, task slice, sample size, metric, value, and protocol. Remove mechanism-causal language from the title/abstract/conclusion unless an ablation isolates it; put the unresolved mechanism in analysis or limitations.
3. Preserve useful author/user edits unless they contradict evidence, current validators, or the operator's latest direction.
4. Do not convert uncertainty into repetitive caveats that make the paper worse. Move detailed scope limits to limitations/discussion.
5. Keep the one-sentence contribution concrete: "We propose X. We show X improves Y by Z because W." If X/Y/Z/W cannot be backed by current artifacts, fix evidence or claims before language polish.
6. The abstract should read like a normal EMNLP abstract. Do not mention validator names, raw paths, evidence-span bookkeeping, review mechanics, or appendix layout trivia in the abstract/body.
7. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.
8. Use official ACL/EMNLP review style, anonymous author block, 7.5--8.0 pages of main content, conclusion by the end of page 8, Limitations and Ethical Considerations after Conclusion, References before Appendix, and a reproducibility appendix. References and Appendix should begin on page 9 or later for an eight-page body; references or appendix material on page 8 are still an underfilled-paper smell. After page 8, the total article length is unrestricted: do not enforce a total-page maximum for References or Appendix. If the body is short, repair by adding or moving source-backed body content before Conclusion: literature-grounded Introduction/Related Work framing, benchmark/Method detail, or evidence-bearing Results/Analysis/Ablation/Failure Cases content according to the page budget. Post-Conclusion end matter does not count as fixing the main body. Do not insert `\clearpage`, `\newpage`, `\pagebreak`, or `\FloatBarrier` immediately before Conclusion; those breaks can strand page 8 and must be reserved for after body end matter.
9. Repair `paper/PAGE_BUDGET.md` and `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` against this reference budget, adjusting only with evidence/exemplar justification: Abstract 0.3 pages; Introduction 1 page; Related Work 0.5--0.8 pages; Method 1--1.5 pages; Experimental Setup 0.5--1 page; Main Results 1--1.5 pages; Analysis/Ablation 1 page; Failure Cases 0.3--0.5 pages; Conclusion 0.2 pages.
10. If the rendered body is underfilled, references begin before page 9, or the paper feels like a thin report, do not fix it with margins, font tricks, filler, or repeated caveats. First check `validate-full-scale-evidence`, `paper/EVIDENCE_GAPS.json`, and `paper/CLAIM_GRAPH.json`; then run missing benchmark conditions, ablations, robustness slices, public-validation checks, or failure analyses. Only expand prose from fresh or already-recorded evidence. If the evidence remains insufficient, downgrade to `pilot-note`/`not_ready` or soften claims.
11. Run `validate-research-md-format` after the final compile and before academic-language/layout review. Update `paper/FORMAT_PREFLIGHT.md` with compile status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
12. Do not tolerate undefined refs/citations, rendered `[?]`, `Overfull \hbox > 5pt`, placeholders, `% UNVERIFIED`, code-like display labels, missing numerical table captions, or stale PDF/log/preflight facts.
13. If `paper/VALIDATION_PRIORITY_POLICY.json` has missing or bad routes, run `python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .` instead of hand-writing a partial policy.
14. After regenerating paper, review, figure, or submission artifacts from current sources, run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` and `python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .`. Manifest entries must be objects with `path`, `sha256`, TSV `columns` when applicable, and generated-artifact `sources`; never use bare-string entries.
15. Do not download model weights, HuggingFace hub files, datasets, or Torch checkpoints into the project workspace. Use the shared host caches injected by argus-skill: `HF_HOME=/root/.cache/huggingface`, `HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub`, `HF_DATASETS_CACHE=/root/.cache/huggingface/datasets`, `TRANSFORMERS_CACHE=/root/.cache/huggingface/hub`, `TORCH_HOME=/root/.cache/torch`, and `XDG_CACHE_HOME=/root/.cache`.

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
2. Data/metric/result plots may be generated locally from scripts. Every other paper-facing figure, including Figure 1, teaser, overall, core method/framework/system/pipeline overview figures, schematics, qualitative/example visuals, and explanatory diagrams, must be the actual generated image-2 raster `output_path`, or equivalent codex-image2 raster, included directly from `paper/main.tex`.
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
