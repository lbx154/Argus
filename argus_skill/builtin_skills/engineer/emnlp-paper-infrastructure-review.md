---
name: EMNLP Paper Infrastructure Review
description: Run the model-backed gate that rejects reader-facing local environment, device, cache, path, and Argus/Codex configuration leaks in an EMNLP/ACL manuscript.
category: paper-review
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-27T00:00:00+00:00
---

## Title
EMNLP Paper Infrastructure Review

## Description
Use this skill when a paper may contain local execution details that do not belong in reader-facing manuscript prose. The check is intentionally delegated to the reviewer model through `paper_infrastructure_review`; do not add ad hoc grep/regex filters for every possible device, cache, or route string.

## When to use
- After editing Method, Experimental Setup, captions, tables, reproducibility appendix, or any configuration prose.
- When validator output mentions `paper_infrastructure_*`, stale infrastructure review hashes, or final readiness is blocked by missing infrastructure review.
- When reviewer/critic suspects environment, device, cache, local path, Argus/Codex daemon, route, or paper-generation details entered the paper.

## What to reject
- Local hardware capacity, ordinals, and device placement such as GPU card numbers, `single local GPU`, local GPU/workstation/node labels, `cuda:6`, `CUDA_VISIBLE_DEVICES`, local device IDs, or node-specific execution notes.
- Local software-environment descriptions that explain the authoring machine rather than the evaluated research system, including CUDA/driver/Python/conda/package tables, runtime environment blocks, or benchmark-machine notes when they are not needed as paper-facing method facts.
- Local cache and filesystem configuration such as `HF_HOME`, `TRANSFORMERS_CACHE`, `TORCH_HOME`, `XDG_CACHE_HOME`, `/root/.cache`, `/root/...`, `/home/...`, or project-private paths.
- Raw runner commands, script names, run IDs, or artifact paths that expose local device/config naming, such as `run_mind2web_gpu.py`, `mind2web-gpu-*`, `.venv`, `--output-root experiments`, `--benchmark-root benchmarks/...`, or project-private experiment directories rendered as the paper-facing reproducibility interface.
- Operational audit-bundle metadata promoted into main-body scientific prose: wall-clock logging, artifact hashes, status snapshots, progress logs, STOP-file cancellation contracts, internal manifest mechanics, provenance-refresh workflow details, or validator/review artifact names. Keep these in appendix replay notes, manifests, logs, or supplementary metadata unless the paper explicitly studies that infrastructure.
- Argus/Codex authoring infrastructure: daemon handoff, engineer/reviewer/critic/scientist routes, capability-vault configuration, validation artifacts, review artifacts, image-tool plumbing, API keys, private endpoints, or `gpt-5.4*` authoring/review routes.
- Any local config table that explains how the paper was generated rather than how the evaluated research system ran.

## What is allowed
- Paper-facing evaluated system facts: model/backend names, benchmark harness, public dataset or benchmark version, task count/split, metric, decoding/budget setting, seed policy, and high-level compute cost when it is genuinely part of reproducibility and not a local machine or authoring-environment description.
- Neutral replay descriptions in the appendix: public benchmark lane, seed policy, split, metric, paper-facing command alias, and concise artifact-type availability. Put exact local CLI strings, path-heavy run IDs, audit-bundle mechanics, hashes, status/progress internals, and STOP-file contracts in manifests/logs/supplementary metadata, not rendered main-body prose.
- Local execution notes in non-rendered comments, manifests, logs, or operator traces. These can support the pipeline, but they must not be rendered in title, abstract, body, captions, tables, or appendix prose.

## How to solve
1. Read the relevant manuscript source:
   - `paper/main.tex`
   - any `\input{}`/`\include{}` section files
   - figure/table captions and appendix prose
2. Remove or rewrite leaks as reader-facing method facts:
   - Replace local device/cache/path text with benchmark protocol, model/backend, metric, budget, and artifact availability.
   - Replace raw local runner paths/run IDs with neutral paper-facing labels such as "Mind2Web primary replay" plus seeds, split, metric, and artifact types.
   - Move wall-clock/hash/status/progress/STOP-file details out of Method, Setup, Results, Analysis, and Conclusion; if needed, summarize them once in appendix-facing replay language.
   - Move local operational details to manifests/logs if they are needed for the daemon, not to the manuscript.
   - Keep evaluated model identifiers only when they describe the experiment itself.
3. Run the model-backed tool:
   - `python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write`
   - `python -m argus_skill.skills.pipeline_contracts validate-paper-infrastructure-review --project-root .`
4. Treat the review files as generated evidence:
   - Do not hand-edit `paper/PAPER_INFRASTRUCTURE_REVIEW.json`, `.md`, or `_history.jsonl`.
   - If the nested `model_review` says `revise`, lists blocking/major issues, reports `leak_free: false`, or leaves revision directives, edit the manuscript and rerun the tool.
5. After a pass, run the surrounding gates affected by source changes:
   - `validate-academic-language-review`
   - `validate-research-md-format`
   - `validate-full-emnlp` near final readiness

## Response shape
- State whether `validate-paper-infrastructure-review` passed.
- If blocked, quote the highest-priority model review issue and the exact source target.
- Mention any paper-facing rewrite made to replace local environment/device/config prose.
