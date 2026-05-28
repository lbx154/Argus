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
This workspace must produce a submission-quality EMNLP/ACL long-paper package, not a pilot PDF, validator-shaped demo, or renamed copy of an older project. Build the paper as an evidence pipeline: research -> plan -> benchmark -> run -> analysis -> draft -> review -> submission.

This is a clean-slate project. Do not inherit titles, claims, datasets, benchmark episodes, generators, figures, review artifacts, result numbers, architecture, thesis, or paper story from any prior project unless they are listed in **Allowed starting inputs** with source, license/access status, allowed use, and rationale.

Non-negotiable research bar: choose a frontier-domain problem grounded in current papers and real benchmark gaps; use the available GPU capacity to train or adapt a substantial domain-appropriate model when the method involves learning; evaluate only on existing real benchmarks or official task/data releases for paper-facing evidence. Synthetic/local benchmarks, tiny bag-of-words scorers, prompt-only wrappers, and exact-oracle policies can be smoke tests or baselines, not the proposed EMNLP-ready result, unless the operator explicitly lowers the scope.

**Research taste is mandatory.** The operator gives you a research DIRECTION, not a paper plan. You must find your own insight:
- Survey the field first. Read code, not just abstracts. Find what existing methods miss.
- Your paper needs a 'WHY' — not just 'we applied X to Y and it worked'.
- Simple reproduction of existing work is NOT a paper. You must have a novel thesis.
- If you can't articulate what makes your approach surprising or counter-intuitive, keep searching.
- Write `research/IDEA_REJECTION_LOG.md` — reject at least one mediocre idea before committing.

## Binding playbooks and validators
- Read and follow the operator-provided research playbook when one is available before choosing the final thesis, benchmark, method name, metric, paper narrative, figure/table design, or final preflight.
- Use the active Argus package/source checkout supplied by the launcher. Built-in skill markdown is available as project-local exports under `./argus_builtin_skills/` and as the Python package resource `argus_skill.builtin_skills`; `ARGUS_SKILL_SOURCE_ROOT` may point at a source checkout, but agents must not hard-code host-specific paths.
- At project setup, copy the built-in skill markdown into this workspace so the daemon can read it directly:
  `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --export-builtin-skills ./argus_builtin_skills`
- Read `./argus_builtin_skills/*.md` and `./argus_builtin_skills/**/*.md` first when invoking built-in paper/research/domain skills. If the local copy is absent or stale, refresh it with the export command above or load `argus_skill.builtin_skills` through the active Python environment. Do not copy the whole Argus repository, global memory, model caches, or capability vault into this project.
- When ownership is unclear, read `./argus_builtin_skills/emnlp-paper-skill-router.md` first, then load the specific skill it routes to.
- Prefer `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill ...` for Argus validation commands; the launcher injects `ARGUS_SKILL_PYTHON`, `ARGUS_SKILL_SOURCE_ROOT`, and `PYTHONPATH` when a source checkout is needed.
- Final EMNLP completion requires this exact command to exit 0 and be quoted in completion evidence:
  `the L2 reviewer marking `done` against the full pipeline checklist (research → submission)`
- Full-scale experiment evidence is a prerequisite for analysis, draft, review, and submission. This command must pass before any of those stages are marked ready/done:
  `the L2 reviewer ticking off the run-stage "full-scale evidence" checklist item`
- Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
- Before final academic/layout review, the paper-quality contracts must pass:
  `the L2 reviewer ticking off the analysis/draft-stage paper-quality checklist items`
- the L2 reviewer pipeline-state checklist item, a compiled PDF, a pilot run, or a passing review artifact alone is not final readiness.

## Skill route
Before each planner or engineer round, classify the current blocker and load only the router plus the focused skill(s) below. Do not skim all skills as a substitute for doing the routed work. If ownership is unclear, read `argus_builtin_skills/emnlp-paper-skill-router.md` first and follow its target skill.

| Current blocker / task | Read this skill first | Use it to decide or produce |
| --- | --- | --- |
| Stage order, readiness state, pivots, or "what next?" | `argus_builtin_skills/auto-research-pipeline.md` | `research/PIPELINE_STATE.json`, stage gates, when to move backward from paper drafting to experiments |
| Benchmark implementation, full-scale runs, baselines, ablations, progress files | `argus_builtin_skills/agent-research-benchmark-runner.md` | runnable harnesses, manifests, `status.json`, `progress.jsonl`, raw scored rows, STOP-file protocol |
| Results analysis, result tables, data figures, Figure 1 / teaser image-2 provenance | `argus_builtin_skills/research-results-analysis-and-figures.md` | `RESULTS_REPORT.md`, result-to-claim tables, paper figures/tables, `IMAGE2_FIGURES.json` |
| Exemplar PDFs, page rhythm, structure blueprint, conformance | `argus_builtin_skills/paper-exemplar-pdf-learning.md` | exemplar PDFs/text, `STYLE_PROFILE.md`, `PAPER_STRUCTURE_BLUEPRINT.md`, structure conformance artifacts |
| First LaTeX draft, citation placement, verified bibliography entries, paper narrative | `argus_builtin_skills/emnlp-paper-drafting.md` | `paper/main.tex`, `PAGE_BUDGET.md`, `PAPER_DRAFT_REPORT.json`, BibTeX connected to claims |
| Short, underfilled, weird-looking, overfull, bad references/appendix/page flow | `argus_builtin_skills/emnlp-format-preflight.md` | classify whether to fix layout/prose or route back to experiments/evidence; compile and page-budget checks |
| Weak claims, unsupported numbers, evidence gaps, stale artifacts | `argus_builtin_skills/claims-evidence-audit.md` | `CLAIM_GRAPH.json`, `EVIDENCE_GAPS.json`, claim-to-result/freshness repair plan |
| Academic tone and model-backed prose critique after evidence is stable | `argus_builtin_skills/emnlp-academic-language-review.md` | fresh `ACADEMIC_LANGUAGE_REVIEW.json` and concrete language directives |
| Iterative paper repair after review feedback | `argus_builtin_skills/paper-review-revision-loop.md` | source-level revisions plus validator reruns, without hand-editing stale generated outputs |
| Final submission readiness and go/no-go | `argus_builtin_skills/research-submission-assurance-gate.md` | `SUBMISSION_ASSURANCE.json`, final PASS/FAIL/BLOCKED decision, exact final validator evidence |

Routing rule: if the blocker is "paper is too short", "format looks fake", "references look bad", or "figure is wrong", first determine whether evidence/full-scale runs/claim support are missing. Missing evidence routes to benchmark execution or analysis before prose/layout polish.

## Operator goal
- Primary paper goal: [write the target research problem and deliverable]
- Target venue/scope: EMNLP/ACL long paper unless the operator explicitly says otherwise
- Success condition: final the L2 reviewer marking done against the full pipeline checklist exit 0 plus a current PDF/submission package
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
| Research | `research/RESEARCH_BRIEF.md`, `research/LITERATURE_REVIEW.md`, `research/LIT_MATRIX.tsv`, `research/LITERATURE_GROUNDING.json`, `research/EXPERIMENT_PLAN.md` |
| Experiments | benchmark source/provenance files, run manifests, `status.json`, `progress.jsonl`, raw result JSON/TSV, logs, STOP-file contract |
| Style references | `paper/style_ref/exemplars/<slug>/paper.pdf`, extracted text, `paper/style_ref/EXEMPLAR.json`, `paper/style_ref/EXEMPLAR_SUITABILITY.json`, `paper/style_ref/STYLE_PROFILE.md`, `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.json`, `paper/style_ref/SOURCES.md` |
| Claim/evidence contracts | `paper/CLAIM_GRAPH.json`, `paper/EVIDENCE_GAPS.json`, claim-to-result tables, result-to-claim tables, and freshness hashes |
| Local Argus skills | `argus_builtin_skills/*.md` and `argus_builtin_skills/**/*.md` exported from the active `argus_skill.builtin_skills` package/source checkout |
| Reviews | `paper/ACADEMIC_LANGUAGE_REVIEW.json`, `paper/LAYOUT_REVIEW.json`, `paper/PAPER_QUALITY_CALIBRATION.json`, `paper/SUBMISSION_ASSURANCE.md`, `paper/SUBMISSION_ASSURANCE.json` |

## Model/API and helper-code contract
1. Model and image credentials are operator capabilities, not project artifacts. The private vault is `~/.argus-skill/capabilities/model_api.json` or `ARGUS_SKILL_CAPABILITY_VAULT`; it should be mode `0600`. Do not manually open/read, print, summarize, copy, or commit its raw contents; only Argus route helpers/tools may load it at runtime.
2. Before model-backed work, run the secret-free status check:
   `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --model-api-status`
   Use the reported routes: `engineer` for code/evaluation helpers, `reviewer` for audits, `image` for image-2/codex-image2 generation, and `image_review` for visual inspection. If a needed route is unavailable but operator-approved environment/Codex config exists, initialize once with:
   `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --init-model-api`
3. Put reusable project wrappers under `code/`; do not scatter raw API calls through notebooks, paper generators, or review JSON writers. Use `load_model_api_route(...)` from Argus, not hard-coded keys, base URLs, or model names. Route-specific environment overrides such as `ARGUS_SKILL_IMAGE_MODEL=gpt-image-2`, `ARGUS_SKILL_IMAGE_BASE_URL`, and `ARGUS_SKILL_IMAGE_API_KEY` may be used only as process environment, never as committed text.
4. Officially launched projects already include `code/llm.py`; prefer that seeded helper over
   rewriting raw HTTP calls. If you must edit or replace it, preserve transient 429/5xx/URL
   retry with exponential backoff and `Retry-After` handling. Do not convert a rate-limit,
   disconnect, or temporary backend error directly into a deterministic fallback answer for an
   experiment row; retry first, then record the failure explicitly if the route is still unusable.
   Minimal `code/llm.py` pattern for text calls:

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

5. For image-2 Figure 1 generation, prefer the Argus image tool and preserve the exact raster it returns:

       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_tool generate \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.png \
         --size 1536x1024 --force
       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_tool inspect \
         --image paper/figures/method_overview.png > paper/figures/method_overview.inspect.json
       "${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.tools.image_tool review \
         --image paper/figures/method_overview.png \
         --prompt-file paper/figures/method_overview.prompt.txt \
         --out paper/figures/method_overview.review.json

   A helper such as `code/generate_image2_figure.py` must then write `paper/figures/IMAGE2_FIGURES.json` with `figure_id`, `figure_type`, `model` or `generator_model`, `prompt_path`, `output_path`, `output_sha256`, `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, width, and height. The sidecar must preserve image-tool/API evidence (`/images/generations`, model, created time, prompt SHA, output SHA, dimensions), and `review_path` must come from the `image_review` model route. `generation_provenance_path` may point at the image sidecar if that JSON records `prompt_path`, `output_path`, and `output_sha256`. Never crop, downsample, resave, PDF-wrap, locally redraw the accepted raster, or hand-fill `codex-image2` metadata around a local PNG after provenance is written.
6. Do not let the model freehand a one-paragraph image prompt. Before calling image-2, create `paper/figures/method_overview.prompt.txt` with `python -m argus_skill.tools.image_tool paper-prompt ...`, keep the required `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a` markers, then generate 6--20 layout variants by changing only the layout/candidate-contract fields; keep the best reviewed raster and record the selected `prompt_variant_id` in provenance or the manifest:

       Use case: scientific-educational
       Prompt template: argus-image2-paper-prompt-v1
       Prompt source: paper-framework-figure-studio-pro-v3.1.4a
       Asset type: Figure 1 teaser / conceptual overview for an EMNLP/ACL/NeurIPS-style academic manuscript.

       General style:
       - EMNLP/ACL/NeurIPS/CS paper method figure, full-width two-column landscape, 1536x1024 or 1920x1088.
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
       - Canvas 1536x1024 or 1920x1088; background #fbfaf7; stroke #1f2933 at 2px.
       - Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
       - Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
       - Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.

   A prompt that lacks `argus-image2-paper-prompt-v1`, `paper-framework-figure-studio-pro-v3.1.4a`, `General style`, `Pinned content`, exact spelling instructions, `Layout variant`, and `Negative prompt / Avoid` is a blocker even if the image API call succeeds.

## Role model
- Planner: decomposes the paper into gated research tasks and chooses the next blocker with the highest reviewer value.
- Engineer: implements benchmarks, experiments, generators, LaTeX, and fixes; edits source/generators rather than patching generated outputs.
- Reviewer: checks evidence, freshness, paper quality, and whether the completion command actually passed.

## Research and experiment plan contract
1. Start from a research brief, not from a paper title.
2. Before selecting the final thesis, survey credible sources: recent high-quality papers, classic anchor papers, benchmark/dataset papers, official repos, and operator-specified trend sources when available.
3. Write `research/LITERATURE_GROUNDING.json` and require at least 10 recent high-quality papers plus at least 3 classic anchors unless access constraints are documented as blockers.
7. Never copy paper or media prose. Store metadata, short paraphrased summaries, and original analysis.

## Training & inference infrastructure contract (research + plan stages)

If the project will involve gradient-based training or large-scale inference,
the agent must select an existing open-source framework on each axis
(training, inference) before the planning stage closes. Self-written
training loops, bare `model.generate()` benchmark loops, and hand-rolled
PPO/GRPO/RLHF trainers are **hard blockers** at the reviewer gate.

1. **Anchor against the bundled baseline:** read
   `argus_builtin_skills/training-infrastructure-guide.md` first. It is
   the operator-curated starting point covering LLM SFT/DPO/RLHF, agent
   RL, diffusion (T2I), LLM inference, and API inference.
2. **Supplement with your own search** of recent arXiv (2026+) and
   GitHub trending for frameworks that match your specific domain. You
   must add at least one credible candidate the bundled guide does not
   name, with URL + last release/commit date + paper (if any).
3. **Maintenance bar:** every shortlisted framework must have a release
   or default-branch commit dated **2026 or later**. Older repos are
   excluded as unmaintained, no matter how prestigious they once were.
4. **Paper-released frameworks are allowed** when (a) the repo meets the
   2026+ recency bar and (b) the paper appears in
   `research/LITERATURE_GROUNDING.json`. Prefer the official authors'
   repo over third-party reimplementations.
5. **No self-written training or inference loops.** Wrap an existing
   framework. Excepted: thin glue scripts that import the framework's
   trainer/inferencer object and configure it.
6. **Research stage artifact:** `research/INFRA_SHORTLIST.md` listing
   every candidate considered with URL, last release/commit date, paper
   (if any), a one-line domain-fit rationale, and a "maintained"
   yes/no — including a note for any bundled-guide entry you found
   stale and excluded.
7. **Plan stage artifact:** `research/INFRA_CHOICE.md` locking in
   exactly one training framework and exactly one inference framework
   with rationale tying the choice to the project domain and the
   GPU / API budget. Mirror the locked choice in
   `research/EXPERIMENT_PLAN.md` under an `## Infra` section. Record
   one explicitly-rejected runner-up with a one-line reason.
8. **Skip both artifacts only if** the project does not train any model
   AND does not run large-scale inference (e.g. a pure literature
   analysis paper). Record the skip explicitly in
   `research/RESEARCH_BRIEF.md`; otherwise the reviewer fails the
   `research.infra_shortlist` and `plan.infra_choice` checklist items.

## Benchmark and experiment contract
1. Final long-paper evidence must come from existing real benchmarks, official benchmark datasets, or official task releases with real ground truth/evaluation. Do not create synthetic benchmarks, generated proxy tasks, hand-written gold graphs, or local pseudo-benchmarks for the main paper claim.
2. Final long-paper evidence must use unique semantic tasks/examples, not duplicated prompts, relabeling, suffixes, paraphrase inflation, or shuffled copies.
3. A small run is pilot evidence only. For final EMNLP readiness, execute a multi-source full-paper matrix over at least 3 independent real benchmark families. A single benchmark slice is not final evidence even when it has hundreds of scored rows.
4. Use the available GPU capacity for a meaningful trained/adapted model when the contribution is learned. Prefer a modern backbone and efficient training recipe (LoRA/QLoRA/FSDP/DeepSpeed/Accelerate as appropriate). Record model family, parameter count, trainable parameters, dataset size, GPU memory plan, GPU-hours, checkpoint/adapter path, and evaluation command.
5. A compact bag-of-words scorer, exact lookahead/oracle policy, lexical ranker, prompt-only wrapper, or trivial classifier is allowed only as a smoke test, baseline, ablation, or operator-approved non-frontier scope. It must not be presented as the main proposed method for a submission-quality long paper.
6. Benchmark construction is not execution. `benchmarks/full/tasks.jsonl`, benchmark manifests, or `status.json task_count` do not satisfy final evidence unless raw completed scored rows under `experiments/**` cover every required method/baseline condition.
7. Use at least 3 independent executed real benchmark/data sources or official task-release families for final long-paper evidence. Multiple slices from one benchmark family, such as Verified/Lite/Multimodal variants of the same suite, count as one source family. `experiments/BENCHMARK_PROVENANCE.md`/`.json` must list selected benchmark sources with name, URL/repo, paper/citation/DOI, version/date, license/access, split/filtering, task count, capability tested, rationale, and execution status. Planned diagnostic rows do not count toward the 3-source final gate.
8. If no local GPU is configured, use the approved hosted LLM route for runnable agent experiments instead of a toy oracle/policy; `gpt-5-mini` is the default low-cost no-GPU backbone unless the operator specifies another model. Record model id, endpoint/provider class, temperature, top_p, max_tokens, token/request budget, seed policy, and stopping rules in internal run manifests. In the paper's Experimental Setup, report only evaluated-system facts such as model/backend, benchmark, metric, budget/decoding, and high-level cost; do not expose local device/cache/path or Argus/Codex route configuration.
9. Synthetic/local tasks are smoke-only. They may test code paths, but their results must not appear in main paper tables, headline metrics, final claims, or submission-readiness artifacts.
10. Include strongest feasible literature/frontier baselines, not only trivial no-skill or lexical baselines. For agent-skill/memory projects, include at least these diagnostic baselines unless the operator documents a domain-specific replacement: `no_skill`, `raw_memory`, `reflexion`, `static_skill_lib`, and the proposed method. Each required condition must be evaluated on the same executed multi-source benchmark matrix used for final-paper claims.
11. Do not optimize toward a fixed small row target or a single convenient slice. The final matrix scale is determined by three independent source families times required conditions; if cost forces a smaller slice, call it pilot/blocked and queue the missing family/condition runs rather than claiming final readiness.
12. Include ablations, failure analysis, confidence intervals or statistical significance, and enough raw logs/results to reproduce every numerical claim.
13. Every long experiment must write `manifest.json`, `status.json`, `progress.jsonl`, logs, raw rows, and a STOP-file cancellation contract. `progress.jsonl` should expose current method, task count, total count, success/failure counts, last heartbeat, and latest artifact path so progress is visible while the daemon is running.
14. the L2 reviewer run-stage "full-scale evidence" checklist item before analysis/drafting; if it fails, write only pilot diagnostics and queue the missing full-run/matrix-completion work.

## Mandatory thick exemplar learning
1. Invoke the Paper Exemplar PDF Learning skill before drafting prose.
2. Download at least two open-access top-conference paper PDFs under `paper/style_ref/exemplars/<slug>/paper.pdf`.
3. At least one exemplar should be a recent EMNLP/ACL best/outstanding/award paper when available; another should match the method/evaluation structure.
4. Extract text to `paper/style_ref/exemplars/<slug>/paper.txt`, compute and record `pdf_sha256`, record license and `pdf_storage_policy`, and write `paper/style_ref/SOURCES.md`.
5. Before locking a primary exemplar, write `paper/style_ref/EXEMPLAR_SUITABILITY.json` scoring candidate exemplars against this project's task type, method family, experiment shape, figure/table density, related-work structure, and page rhythm. the L2 reviewer draft-stage exemplar checklist item; a weak exemplar match is a drafting blocker.
6. `paper/style_ref/EXEMPLAR.json` must use `exemplar_schema_version: 2` and include `local_pdf`, `text_extract`, `pdf_sha256`, `license`, `pdf_storage_policy`, `usage: "structural_style_only"`, and `no_prose_copy: true` for every exemplar.
7. Write a thick `paper/style_ref/STYLE_PROFILE.md` covering abstract shape, section/page allocation, figure/table inventory, related-work shape, evaluation layout, formatting/layout lessons, writing lessons, transfer plan, and no-prose-copy policy.
8. Write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` before prose. It must map exemplar lessons to this paper's section order, page budget, paragraph roles, figure/table plan, related-work grouping, evaluation sequence, and local evidence mapping. Draft the paper by following this exemplar-derived skeleton directly; title and section names may adapt to the current thesis, but the page rhythm and role sequence should not drift without explicit evidence.
9. After drafting, write `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` from the actual `paper/main.tex` section order. The JSON must use `conformance_schema_version: 1`, `verdict: "PASS"`, `no_prose_copy_attestation: true`, at least two `exemplar_lessons`, and `section_mappings` for every final top-level section before References/Appendix.
10. Every section mapping must include `maps_to_exemplar_phase`, `evidence_sources`, `exemplar_lesson`, and a paper-specific `deviation_rationale` for nonstandard sections. The paper may adapt exemplar architecture to the current thesis, but unmapped/freehand filler sections such as `Protocol Notes`, `Track Mechanics`, `Release Detail`, `Mechanics`, or `Notes` are blockers.
11. Run `the L2 reviewer ticking off the draft-stage exemplar/structure checklist item`; URL-only exemplars and missing structure blueprints are blockers. Final readiness additionally checks `STRUCTURE_CONFORMANCE`.
12. Use exemplars only for structural style learning. Do not copy prose, examples, terminology, claims, bibliography text, figure design, or sentence templates.

## Paper narrative and prose contract
1. Do not write the abstract first. Draft the abstract after the main numbers, ablations, and limitations exist.
2. The paper must have one sentence-long contribution: "We propose X. We show X improves Y by Z because W." If X, Y, Z, and W cannot be filled from evidence, the paper is not ready.
3. The abstract should read like a normal EMNLP abstract: problem, gap, method, result, implication. Do not expose validator names, raw paths, evidence-span bookkeeping, review mechanics, or appendix layout trivia in the abstract/body.
4. Every numerical paper claim must trace to raw artifacts under `results/`, `experiments/`, or `paper/artifacts/`.
5. Paper writing and experimentation may interleave. If drafting exposes weak or missing evidence, stop claiming readiness, run the needed supplement/ablation/error analysis, and then update the claim graph and paper; if the evidence remains weak, soften or remove the claim instead of cherry-picking.
6. Keep claims calibrated without turning the paper into repetitive defensive caveats. Move detailed scope limits to limitations/discussion.
7. Never invent BibTeX. Fetch/verify references through scholarly sources or mark unresolved entries as blockers.
8. The main Results section must contain one large paper-facing results matrix, preferably a `table*`, that spans the selected benchmark families and all major methods/baselines. It must have human labels and columns such as `Benchmark / source family`, `Task count / split`, `Evaluated model/backend`, `Method or baseline`, `Metric`, `Budget/decoding`, and the key result. This table is the reader's central map of the three-benchmark evidence; do not replace it with separate tiny audit tables or internal route logs.

## Paper-quality contract files
1. `paper/CLAIM_GRAPH.json` must bind every major claim to its section, required evidence, raw result artifact, figure/table/citation support, and allowed fallback if evidence is weak. `paper/EVIDENCE_GAPS.json` must list missing or weak evidence and the planned supplement, ablation, negative result framing, or claim downgrade.
2. `paper/FIGURE_TABLE_STYLE_GUIDE.json` must specify the intended body/appendix float inventory, width, font/readability target, legend/caption length, color discipline, column density, information hierarchy, and whether each float belongs in the main body or appendix. Ugly, cramped, or audit-table-like floats are blockers even if the PDF compiles.
3. `paper/VALIDATION_PRIORITY_POLICY.json` must order repair work as freshness, full-scale experiment evidence, claim evidence, and content sufficiency first; exemplar structure next; figure/table and format/layout next; academic language only after evidence and structure are stable; manifest/readiness cleanup last. It must include every validator failure class, not only the currently failing ones. Run `python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .` to create the standard scaffold before final review loops. Underlength, underfilled body, missing full-scale runs, missing baselines, weak ablations, or missing failure analysis are not layout-only problems: route them to `run_more_experiments`, additional ablations/failure studies, source-backed Introduction/Related Work/Method expansion, or evidence-backed analysis according to the actual gap. After repeated non-improving edits, reset the skeleton/float plan instead of looping on review JSON or cosmetic micro-edits.
5. the L2 reviewer analysis/draft-stage paper-quality checklist items before final academic-language and layout review. Missing, stale, or thin contract artifacts are hard blockers.

## Citation and related-work contract
1. Use starter citation targets only when the topic matches. Treat keys as retrieval targets, not as ready BibTeX: verify each entry through Semantic Scholar, arXiv, CrossRef, ACL Anthology, DBLP, or official project pages.
2. Keep references separated by claim/topic/section. Each related-work paragraph must cite the specific papers it discusses; do not dump all citations into one dense paragraph, one mega-sentence, a caption, or the bibliography with no local discussion.
3. Maintain a literature matrix with topic, paper key, verified source, claim supported, and intended paper section before drafting related work.
4. Use ACL/EMNLP author-year natbib style for review submissions. Do not add `\setcitestyle{numbers,square}`, `\usepackage[numbers]{natbib}`, or other numeric citation overrides unless the operator explicitly changes the venue/style requirement.
5. Verify references semantically, not only by compiling: citation key, title, authors, year, venue, DOI/arXiv/ACL URL, and rendered bibliography entry must refer to the same paper. Missing author/editor/organization metadata is a blocker because it renders title-only labels. If a starter key maps to an unrelated title, refetch the metadata instead of renaming the entry.
6. Starter targets for memory, agent-skill, and hallucination papers:
   - Tool-use and agent loops: `yao2023react`, `shinn2023reflexion`, `madaan2023selfrefine`, `schick2023toolformer`, `qin2023toolllm`, `li2023apibank`, `patil2023gorilla`, `shen2023hugginggpt`, `karpas2022mrkl`.
   - Memory, skills, and long-horizon agents: `wang2024voyager`, `zhao2024expel`, `packer2023memgpt`, `park2023generativeagents`, `xu2025amem`, `zhong2024memorybank`, `wang2023longmem`.
   - Self-evolution and process supervision: `qi2024webrl`, `li2025webevolver`, `wang2025mobileagente`, `tang2025sage`, `zhang2025skillrl`, `lightman2023letsverify`, `zelikman2022star`.
   - Evaluation, hallucination, and multi-agent surveys: `zheng2023judging`, `ji2023survey`, `huang2025hallucination`, `guo2024llmmas`, `manakul2023selfcheckgpt`, `lin2022truthfulqa`.
   - Agent benchmarks and validation environments: `liu2023agentbench`, `zhou2023webarena`, `mialon2023gaia`, `maharana2024locomo`, `shridhar2020alfworld`.
7. Add domain-specific EMNLP/ACL papers, benchmark papers, dataset papers, and official repos from the literature survey until the final paper clears the bibliography-depth gate. Unrelated domains need their own topic-specific starter list.

## Paper formatting and layout contract
1. Use the official ACL/EMNLP style files, preferably `\usepackage[review]{acl}`, and the anonymous review author block `Anonymous EMNLP Submission`.
2. The final paper must be an EMNLP/ACL long paper with 7.5--8.0 pages of main content, verified citations, limitations, ethics, and a reproducibility appendix. References and Appendix must begin on page 9 or later, and there is no total-page maximum after the body. It must be readable to an outside reviewer without project-local context: Method/Experimental Setup must name the **evaluated paper system** and its paper-facing agent framework, benchmark harness, or controller; controller/skill/memory mechanism; task source/version; baselines; metrics; budget; and stopping/resume rules. LLM/model identifiers are mandatory for evaluated hosted agent calls, while scorer-based experiments should name the evaluated scorer/backend such as `PairScorer` and its scoring budget. Do not describe Argus, Codex engineer/reviewer routes, daemon handoff, academic-language/layout review, local device/cache/path configuration, or image-tool infrastructure as if they were components of the paper method. Use a compact system/configuration table when this would otherwise be buried in prose; never expose API keys, private endpoints, or raw capability-vault contents.
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

   Section-depth before final readiness is reviewer-calibrated, not an exact word-count floor. Keep the abstract in the normal 170--220 word range. The Introduction needs cited prior-work/benchmark hooks, problem/gap framing, method insight, quantified result preview, contribution roadmap, and scope; Method and Experimental Setup need enough evaluated-system, benchmark, metric, budget/decoding or scoring, seed-policy, and stopping-rule detail for a reviewer to understand the work. A body can be eight pages and still fail if it reaches the target through repeated caveats, formulaic contrast sentences, stale result numbers, oversized floats, or post-Conclusion material.

4. Conclusion must appear by the end of page 8 and should not render before page 7 for a full long paper. References and Appendix should begin on page 9 or later; references or appendix material on page 8 usually mean the paper has only about seven pages of body. If the body is short, add or move source-backed body content before Conclusion: literature-grounded Introduction/Related Work framing, benchmark/Method detail, or evidence-backed Results/Analysis/Ablation/Failure Cases content according to the page budget. Limitations, Ethical Considerations, release notes, references, or appendix content after Conclusion do not fix an underfilled main body. References must appear before Appendix and start cleanly after the eight-page body. Do not cap total pages after the reference/appendix boundary. Never put `\clearpage`, `\newpage`, `\pagebreak`, or `\FloatBarrier` immediately before Conclusion; use those only after body end matter when a clean bibliography/appendix boundary is needed.
5. the L2 reviewer review-stage checklist item for research.md format after final compile and before academic-language/layout review.
6. Write `paper/FORMAT_PREFLIGHT.md` with compile command/status, page count, conclusion page, figure/table inventory, bibliography status, fixes, and final validator result.
7. No undefined references/citation warnings, no rendered `[?]`, no `Overfull \hbox > 5pt`, no placeholders/TODO/TBD/FIXME, no `% UNVERIFIED`, and no ugly code-like display labels in title, abstract, headings, captions, figures, or tables.
8. Body figures <=5 total, at most one `figure*`, every figure labeled and referenced, every table caption has a numerical headline, middle-body visual rhythm passes the model-backed layout review, and at least one paired-significance table when comparative binary outcomes apply.
9. The body should include a large main results matrix that covers all selected benchmark families and major baselines in one place; it should be visually professional rather than a dump of validator artifacts. Split only if the table cannot fit cleanly under the overfull/legibility contract, and keep the cross-benchmark summary in the body while moving low-value diagnostics to the appendix.
10. Tables must follow the `research.md` style tokens: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent only for meaningful degradation, and bold winning values.

## Figure contract
1. Use image-2/codex-image2 for at least one core conceptual figure.
2. Data/metric/result plots may be generated locally from scripts. Every other paper-facing figure, including Figure 1, teaser, overall, core method/framework/system/pipeline overview figures, schematics, qualitative/example visuals, and explanatory diagrams, must include the actual generated image-2 raster `output_path` directly from `paper/main.tex`.
3. Preserve prompt, metadata, generation provenance, inspect/review artifacts, SHA-256, width, and height. Do not crop, downsample, resave, overwrite, redraw, trace, vectorize, PDF-wrap, screenshot, or relabel the image after provenance is written.
4. Do not replace the overview with matplotlib/FancyBboxPatch, TikZ node graphs, PIL/SVG/HTML canvases, manual vector tools, cleaned PDF derivatives, or locally drawn mockups. If it is ugly, regenerate through image-2 from `python -m argus_skill.tools.image_tool paper-prompt ...`, keep `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a`, then review and run `sync-paper-metadata`.
5. Conceptual figures must be adaptive or landscape page-width assets, preferably `1536x1024` or `1920x1088` (image-route dimensions divisible by 16); do not use square `1024x1024`, weird/sketchy fonts, tiny text, heavy gradients, photorealism, excessive logos, or decorative clutter.
6. Data figures and tables must be generated from local raw data/results, not from image-2.

## Final review and assurance
1. Run `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` and confirm the L2 reviewer ticks the review-stage "academic language" checklist item. The generated `paper/ACADEMIC_LANGUAGE_REVIEW.json` must score at least 4/5, be model-backed, fresh, contain quoted evidence spans, and have `needs_revision: false` with no active directives.
2. Run `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` and confirm the L2 reviewer ticks the review-stage "layout" checklist item. `paper/LAYOUT_REVIEW.json` must score at least 4/5, be vision-backed from rendered PDF page snapshots, fresh, and `needs_revision: false`.
3. Write `paper/SUBMISSION_ASSURANCE.md` and `paper/SUBMISSION_ASSURANCE.json`.
4. Review artifacts, calibration files, and readiness reports are evidence, not optimization targets. Never hand-edit them to say PASS/ready while underlying validators fail.
5. Never emit PASS/WARN/final-ready if any required validator fails. `WARN` cannot launder pilot scale, stale reviews, missing provenance, or known hard blockers into final EMNLP readiness.

## Operational safety
1. Work inside this project directory unless reading an operator-provided research playbook or the active Argus source/package through the launcher-provided environment.
2. Never copy parent workspaces, the Argus repository, `.skill-agent`, `.argus-skill`, `.cache`, model caches, capability vaults, or recursive workspaces into this project.
3. **Model weight storage:** if you download any model checkpoint, adapter, tokenizer, embedding, or dataset, put it under `./models/` inside this project. Set the HuggingFace / PyTorch cache environment to point there: `HF_HOME=$(pwd)/models/huggingface`, `HUGGINGFACE_HUB_CACHE=$(pwd)/models/huggingface/hub`, `HF_DATASETS_CACHE=$(pwd)/models/huggingface/datasets`, `TRANSFORMERS_CACHE=$(pwd)/models/huggingface/hub`, `TORCH_HOME=$(pwd)/models/torch`. Each project owns its weights; do not pollute the shared `~/.cache/` of the host or another project. Add `models/` to `.gitignore` if it is not already there so checkpoints never enter git history. Skip the download entirely if the model is already addressable through the model API route in `~/.argus-skill/capabilities/model_api.json`.
4. Keep API keys and capability vault contents out of all artifacts.
5. Record meaningful decisions and evidence in project files, not only in chat.
6. Preserve user edits and unrelated work. Do not revert files you did not intentionally change.

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

The full project is complete only when the final the L2 reviewer marking done against the full pipeline checklist command above exits 0 on the current workspace and that exact output is quoted in completion evidence.
```

## Generality check
This template is EMNLP/ACL-paper-specific but must stay project-neutral. It must not contain host-specific Argus paths, a specific project title, benchmark name, result number, figure name, or prior-workspace story.

## Coverage check
Before using the template, fill all bracketed placeholders, list allowed inputs, and delete no hard gate unless the operator explicitly changes the paper scope.
