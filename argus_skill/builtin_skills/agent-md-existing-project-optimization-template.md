---
name: AGENTS.md Existing Project Optimization Template
description: Copy-ready AGENTS.md template for repairing or optimizing an existing venue-aware AI research paper project without erasing useful evidence or gaming validators.
category: project-agent-template
version: 2
---

## Title
AGENTS.md Existing Project Optimization Template

## When to use
- Use this when a project already has research artifacts, code, experiments,
  LaTeX, figures, reviews, user edits, or an accepted AI research direction.
- Use it for rescue, hardening, validation cleanup, evidence refresh, paper polish, experiment completion, image-2 figure replacement, format preflight, and final submission-readiness loops.

## When NOT to use
- Do not use this when the operator explicitly rejected the current direction and asked for a clean-slate paper. Use the new-project template instead.
- Do not preserve a bad prototype merely because artifacts exist; if the thesis, benchmark, or paper story is invalid, document the rejection and create a clean-slate reset contract that lists only raw evidence allowed to carry over.
- Direction rule: the operator's most recent explicit instruction wins. Prefer this template when the operator asks to continue, rescue, repair, or optimize the current project, or when current artifacts remain authoritative. Switch to a clean-slate contract only when the operator rejects the current direction or the audit proves the thesis/architecture must be abandoned; raw data may still be selectively listed as allowed input for the new project.

## Copy-ready `AGENTS.md`

```markdown
# AGENTS.md

## Project contract
This is an existing venue-aware AI research workspace. Improve the current
paper package while preserving valid source, raw evidence, experiment logs,
tests, user edits, and operator-approved decisions.

The goal is a submission-quality long paper, not a pilot PDF, validator-shaped demo, or superficial review-file edit. Keep the scientific chain coherent: research question -> primary literature -> benchmark/code -> experiment runs -> analysis -> tables/figures -> manuscript -> rendered PDF -> independent final paper review.

## Binding playbooks and validators
- Read and follow the operator-provided research playbook when one is available before changing the thesis, benchmark, method name, metric, paper narrative, figure/table design, or final preflight.
- Use the active Argus package/source checkout supplied by the launcher. Built-in skill markdown is available as project-local exports under `./argus_builtin_skills/` and as the Python package resource `argus_skill.builtin_skills`; `ARGUS_SKILL_SOURCE_ROOT` may point at a source checkout, but agents must not hard-code host-specific paths.
- If this workspace does not already have local copies, export the built-in skills so the daemon can read them directly:
  `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --export-builtin-skills ./argus_builtin_skills`
- Read `./argus_builtin_skills/*.md` and `./argus_builtin_skills/**/*.md` first when invoking built-in paper/research/domain skills. If the local copy is absent or stale, refresh it with the export command above or load `argus_skill.builtin_skills` through the active Python environment. Do not copy the whole Argus repository, global memory, model caches, or capability vault into this project.
- When ownership is unclear, resolve the target venue first, then load that
  venue's drafting/preflight/review router; do not hard-code EMNLP.
- Prefer `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill ...` for Argus validation commands; the launcher injects `ARGUS_SKILL_PYTHON`, `ARGUS_SKILL_SOURCE_ROOT`, and `PYTHONPATH` when a source checkout is needed.
- Final completion is certified only when the L2 reviewer returns a
  `scope: final_submission` verdict with `status: done` and concrete evidence
  covering the full pipeline and selected venue.
- Full-scale experiment evidence is a prerequisite for analysis, narrative, drafting, final review, and submission. The L2 reviewer must tick off the run-stage "full-scale evidence" checklist item before any of those stages are marked ready/done.
- Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
- the L2 reviewer pipeline-state checklist item, a compiled PDF, a pilot run, or a passing stale review artifact alone is not final readiness.

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
| Paper source | LaTeX/generator/source tables | `paper/main.tex`, `paper/main.pdf`, submission copy | compile, the L2 reviewer review-stage checklist item for research.md format |
| Figures | image-2 prompts/provenance and data plotting scripts | raster overview, data plots, figure manifest | image review, layout review, artifact manifest |
| Review | current manuscript, PDF, raw results, and primary sources | source revisions and optional tool feedback | the L2 Reviewer's independent final paper judgment |

If generated artifacts and source disagree, treat source/generator plus raw evidence as authoritative. Regenerate downstream artifacts after source changes, refresh manifests, then rerun the relevant review/validator.

## Model/API and helper-code repair contract
1. Model and image credentials are operator capabilities, not project artifacts. The private vault is `~/.argus-skill/capabilities/model_api.json` or `ARGUS_SKILL_CAPABILITY_VAULT`; it should be mode `0600`. Do not manually open/read, print, summarize, copy, or commit its raw contents; only Argus route helpers/tools may load it at runtime.
2. Before model-backed repair or review work, run the secret-free status check:
   `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --model-api-status`
   Use the reported routes: `author` for literature/claim synthesis, `engineer` for code/evaluation helpers, `reviewer` for audits, `image` for image-2/codex-image2 generation, and `image_review` for visual inspection. If a needed route is unavailable but operator-approved environment/Codex config exists, initialize once with:
   `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --init-model-api`
3. Keep or create reusable wrappers under `code/`; do not scatter raw API calls through paper generators or review JSON writers. Use `load_model_api_route(...)` from Argus, not hard-coded keys, base URLs, or model names. Route-specific environment overrides such as `ARGUS_SKILL_IMAGE_MODEL=gpt-image-2`, `ARGUS_SKILL_IMAGE_BASE_URL`, and `ARGUS_SKILL_IMAGE_API_KEY` may be used only as process environment, never as committed text.
4. No model wrapper is guaranteed to exist. Reuse a sound project helper when present;
   otherwise create a small project-owned helper such as `code/llm.py` instead of
   scattering raw HTTP calls through generators or experiment code. Preserve transient
   429/5xx/URL retry with exponential backoff and
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

       def complete(prompt: str, *, route_name: str = "author", system: str = "") -> str:
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

       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_api generate \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.png \
         --size 1536x1024 --force
       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_api inspect \
         --image paper/figures/method_overview.png > paper/figures/method_overview.inspect.json
       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.verticals.research.figure_tool review \
         --image paper/figures/method_overview.png \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.review.json

   A helper such as `code/generate_image2_figure.py` must then write or refresh `paper/figures/IMAGE2_FIGURES.json` with `figure_id`, `figure_type`, `model` or `generator_model`, `prompt_path`, `output_path`, `output_sha256`, `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, width, and height. The sidecar must preserve image-tool/API evidence (`/images/generations`, model, created time, prompt SHA, output SHA, dimensions), and `review_path` must come from the `image_review` model route. `generation_provenance_path` may point at the image sidecar if that JSON records `prompt_path`, `output_path`, and `output_sha256`. Never crop, downsample, resave, PDF-wrap, locally redraw the accepted raster, or hand-fill `codex-image2` metadata around a local PNG after this provenance is written.
6. If the current Figure 1/teaser uses image-2 and is ugly, cramped, misspelled, square, generic, or prompt-thin, regenerate through image-2 from `python -m argus_skill.verticals.research.figure_tool paper-prompt ...`, using it as the recommended canonical prompt (carrying the `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a` markers), generating as many layout variants as needed (up to 20) by changing only the layout/candidate-contract fields; keep the best reviewed raster and record the selected `prompt_variant_id` in provenance or the manifest. For any other recorded renderer, repair its source and rerender through the Research Visualization Router:

       Use case: scientific-educational
       Prompt template: argus-image2-paper-prompt-v1
       Prompt source: paper-framework-figure-studio-pro-v3.1.4a
       Asset type: Figure 1 teaser / conceptual overview for the selected venue.

       General style:
       - Selected-venue AI paper method figure, adaptive landscape, 1536x1024 or 1920x1088.
       - Clean Figma-style block diagram / block-based Figma style with rounded cards, neat alignment, soft pastel fills, dark-gray 2px borders, and compact information density.
       - Compact, information-rich, suitable for a PDF page-width figure; little wasted space but not crowded.
       - Tidy rounded handwritten or friendly sans-serif feel is acceptable only if it remains crisp and readable; no messy sketch fonts.
       - Moderate badge/icon use only when semantically useful; a few simple recognizable icons are fine, not a logo wall.
       - No heavy shadows, no gradients, no photorealism, no glassmorphism, no messy Excalidraw look.
       - Large readable labels, short phrases, balanced hierarchy, flat vector-like raster rendering on warm white #fbfaf7.

       Style intent:
       - Clean, dense, modular, Figma-like, mostly rounded cards, low-saturation pastel blocks.
       - Use small badges/icons sparingly; avoid empty space while preserving alignment.
       - It should look like a main figure in the selected venue, not a marketing graphic, stock illustration, dashboard screenshot, or casual whiteboard.

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
       - Canvas 1536x1024 or 1920x1088; background #fbfaf7; stroke #1f2933 at 2px.
       - Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
       - Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
       - Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.

   A prompt that lacks `argus-image2-paper-prompt-v1`, `paper-framework-figure-studio-pro-v3.1.4a`, `General style`, `Pinned content`, exact spelling instructions, `Layout variant`, and `Negative prompt / Avoid` is a blocker even if the image API call succeeds.

## Role model
- Planner: chooses the next blocker with the highest reviewer value, not the easiest cosmetic edit.
- Engineer: fixes one bounded blocker end-to-end, updates generators when needed, and reruns relevant validation.
- Reviewer: verifies the claimed blocker is actually gone and no new blocker was introduced.

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
6. Refresh manifests, reviews, and preflight artifacts after their source or PDF changes.
7. Run targeted validation for the blocker.
8. Run broader validation if the change affects shared source, public behavior, paper readiness, experiment claims, figure provenance, or review hashes.
9. Stop only when the blocker is gone or when a new operator decision is required.

## Existing research and evidence repair
1. Preserve valid raw results and provenance, but do not preserve weak claims, stale reviews, copied text, duplicated benchmark rows, or known-invalid benchmark framing.
2. If the current evidence is only pilot-scale, label it as pilot evidence and queue a real scale-up run; do not pad the paper or review metadata into final readiness.
3. A focused public source may support a narrow final claim when controls and
   uncertainty justify it; broader claims require broader public validation.
4. Benchmark construction is not execution. `benchmarks/full/tasks.jsonl`, benchmark manifests, or `status.json task_count` do not satisfy final evidence unless raw completed scored rows under `experiments/**` cover every required method/baseline condition.
5. Benchmark scale must come from unique semantic tasks/examples, not duplicates, relabeling, suffixes, paraphrase inflation, or shuffled copies.
6. For agent-skill/memory projects, each required baseline/method condition such as `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method must be evaluated on the same executed multi-source benchmark matrix, unless the operator documents a domain-specific replacement.
7. Final empirical claims need executed evidence from appropriate public
   benchmark/data/task sources. Scope and source count follow the claim; synthetic
   diagnostics alone are not final public evidence.
8. Benchmark/source selection must document source diversity, recency/relevance, adoption/rejection decisions, license/access status, leakage controls, and why each source tests a distinct capability.
9. Use local compute, hosted models, CPUs, accelerators, simulators, theorem
   provers, or other resources only when appropriate to the research design.
10. GPU utilization is an efficiency observation, not a universal scientific gate.
11. Every numeric claim must remain tied to current raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
12. the L2 reviewer run-stage "full-scale evidence" checklist item before final analysis/drafting/review. If it fails, preserve valid raw evidence but queue the missing full-run or matrix-completion work and keep the PDF non-final.
13. If valid evidence rejects the method-positive thesis, decide whether the
    negative, diagnostic, or boundary finding has research value. Write it
    honestly when it does; pivot only when broken, inconclusive, or not useful.

## Existing paper repair
1. Improve the artifact the reader/reviewer sees, not just the validator
   surface. Method/Experimental Setup must describe the actual model, algorithm,
   system, data, proof, evaluator, diagnostic protocol, public evidence, and
   relevant configuration for the claim.
2. If academic-language review repeatedly says the headline mechanism is unsupported or not isolated, reset the claim instead of polishing the same sentence again. Use one exact end-to-end result as the paper's headline: method/system name, comparator, task slice, sample size, metric, value, and protocol. Remove mechanism-causal language from the title/abstract/conclusion unless an ablation isolates it; put the unresolved mechanism in analysis or limitations.
3. Preserve useful author/user edits unless they contradict evidence, current validators, or the operator's latest direction.
4. Do not convert uncertainty into repetitive caveats that make the paper worse. Move detailed scope limits to limitations/discussion.
5. Keep the one-sentence contribution concrete: "We propose X. We show X improves Y by Z because W." If X/Y/Z/W cannot be backed by current artifacts, fix evidence or claims before language polish.
6. The abstract should follow the selected venue's normal research-paper style
   and must not expose validator or authoring-infrastructure details.
7. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.
8. Use the selected venue's official style, anonymity, page/word limits,
   required sections, bibliography rules, and reproducibility contract from
   `research/VENUE_PROFILE.json`.
9. Repair `paper/PAGE_BUDGET.md` and `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` against this reference budget, adjusting only with evidence/exemplar justification: Abstract 0.3 pages; Introduction 1 page; Related Work 0.5--0.8 pages; Method 1--1.5 pages; Experimental Setup 0.5--1 page; Main Results 1--1.5 pages; Analysis/Ablation 1 page; Failure Cases 0.3--0.5 pages; Conclusion 0.2 pages.
10. If the rendered body is underfilled, references begin before page 9, or the paper feels like a thin report, do not fix it with margins, font tricks, filler, or repeated caveats. First check the L2 reviewer run-stage "full-scale evidence" checklist item, `paper/EVIDENCE_GAPS.json`, and `paper/CLAIM_GRAPH.json`; then run missing benchmark conditions, ablations, robustness slices, public-validation checks, or failure analyses. Only expand prose from fresh or already-recorded evidence. If the evidence remains insufficient, downgrade to `pilot-note`/`not_ready` or soften claims.
11. the L2 reviewer review-stage checklist item for research.md format after the final compile and before academic-language/layout review. Update `paper/FORMAT_PREFLIGHT.md` with compile status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
12. Do not tolerate undefined refs/citations, rendered `[?]`, `Overfull \hbox > 5pt`, placeholders, `% UNVERIFIED`, code-like display labels, missing numerical table captions, or stale PDF/log/preflight facts.
13. Do not commit model weights, HuggingFace hub files, datasets, or Torch checkpoints to git. Download them into the project-local store under `./models/` (pre-created by the launcher and gitignored) by pointing the cache variables there: `HF_HOME=$(pwd)/models/huggingface`, `HUGGINGFACE_HUB_CACHE=$(pwd)/models/huggingface/hub`, `HF_DATASETS_CACHE=$(pwd)/models/huggingface/datasets`, `TRANSFORMERS_CACHE=$(pwd)/models/huggingface/hub`, and `TORCH_HOME=$(pwd)/models/torch`. Each project owns its weights (see the training-infrastructure-guide skill).

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
5. Add domain- and venue-relevant papers, benchmark/data papers, and official
   repos until the 35/30 bibliography-depth gate and claim coverage both hold.

## Figure repair
1. Use the Research Visualization Router and inspect the actual visible figure;
   optional `FIGURE_PROVENANCE.json` may help locate its source.
2. Repair only unreadable, factually wrong, broken, or seriously unattractive
   figures. Pass good-enough visuals and avoid repeated aesthetic regeneration.
3. For actual image-2 figures, preserve prompt, generation/inspect/review
   sidecars, exact accepted raster hash, width/height, and
   `IMAGE2_FIGURES.json`. Never relabel a local file as image-2 or patch only
   metadata.
4. Image-2 absence is not itself a blocker. Use a truthful deterministic route
   when it can express the same scientific content.
5. Review every repaired figure at final paper size; optional source/renderer
   metadata is advisory only.

## Exemplar/style repair
1. If `paper/style_ref/EXEMPLAR.json` is absent, URL-only, stale, or schema-incomplete, invoke the Paper Exemplar PDF Learning skill before paper prose polish.
2. Ensure at least two open-access top-conference exemplar PDFs exist under `paper/style_ref/exemplars/<slug>/paper.pdf`, with extracted text, `pdf_sha256`, license, `pdf_storage_policy`, `usage: "structural_style_only"`, and `no_prose_copy: true`.
3. Refresh `paper/style_ref/STYLE_PROFILE.md` when the target venue, paper structure, method/evaluation style, or exemplar set changes.
4. Refresh `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` before prose repair. It must map exemplar lessons to the current paper's section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, and local evidence mapping.
5. Rebuild `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual final `paper/main.tex` section order after repair. Use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every top-level section before References/Appendix.
6. The repair target is not to preserve messy filler. Remove or merge unmapped sections such as `Protocol Notes`, `Track Mechanics`, `Release Detail`, `Mechanics`, or `Notes`; if a nonstandard paper-specific section is genuinely necessary, map it with `maps_to_exemplar_phase`, cite local `evidence_sources`, attach an `exemplar_lesson`, and write a `deviation_rationale`.
7. Run `the L2 reviewer ticking off the draft-stage exemplar/structure checklist item`; URL-only exemplars and missing structure blueprints remain blockers. Final readiness additionally checks `STRUCTURE_CONFORMANCE`.
8. Use exemplars only for structure. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

## Final paper review
1. Read the current manuscript and rendered PDF as a venue reviewer.
2. Check doubtful material claims against raw results or primary sources.
3. Run language or layout tools only when they answer a concrete unresolved
   question; their generated files are advisory.
4. Let the L2 Reviewer decide readiness from the paper and its sources. Do not
   write an assurance packet or optimize generated review scores.

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

The full project is complete only when the L2 reviewer certifies `done` for `scope: final_submission` against the full pipeline checklist on the current workspace, with every checklist item satisfied and backed by concrete evidence, and that verdict is quoted in completion evidence.
```

## Generality check
This template is venue-aware and project-neutral. It must not contain
host-specific Argus paths, a specific project title, benchmark name, result
number, figure name, or prior-workspace story.

## Coverage check
Before using the template, fill the current operator goal, canonical state table, validation commands, and reset boundary from the actual project. Delete no hard gate unless the operator explicitly changes the paper scope.
