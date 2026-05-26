# TB2 current evidence status

> **Purpose**: summarize the current 2026-05-15 TB v2 evidence state using only
> repo-local artifacts, with clean archive bundles separated from broken
> detached evidence.

## Safe-to-cite archived bundles

- [`benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/)
- [`benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/)
- [`benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/)
- [`benchmarks/evidence/tb2-manual-followup-20260515T202500Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-manual-followup-20260515T202500Z/)

These bundles are the durable evidence layer. They preserve `PLAN.md`,
`BUILD_INFO.md`, `manifest.json`, `summary.tsv`, `RESULTS.md`, `logs/`, and
`jobs/index.tsv`, and they are the right source for reward, wall-time, and
manual-attention summaries.

## Healthy auth-fixed smoke

- [`experiments/tb2-harbor-smoke-20260515T235537Z/`](/home/argustest/argus-skill/experiments/tb2-harbor-smoke-20260515T235537Z/)
- The detached smoke completed with `status.json.state=completed`, a non-empty
  trial `result.json`, and an official verifier `reward.txt`.
- Its Codex transcript does not contain `302 Found` or `401 Unauthorized`.

This smoke is the current control that shows the detached auth/base-url wiring
works when the launcher snapshots Codex auth correctly.

## Broken detached evidence

- [`experiments/tb2-argus-v12-redux-20260515T211403Z/`](/home/argustest/argus-skill/experiments/tb2-argus-v12-redux-20260515T211403Z/)
- Its `status.json` is still `running`, so it is not a completed comparison run.
- The job transcripts under `jobs/2026-05-15__21-14-04/.../agent/codex.txt`
  show repeated `302 Found` websocket redirects followed by `401 Unauthorized`
  errors against `https://ai4m6.openai.azure.com/openai/v1/responses`.
- The archived TB2 summary bundles still record `docker_address_pool_exhaustion`
  for the 2026-05-15 fullbench comparison, so the current evidence remains
  broken on the infrastructure axis even when reward artifacts are preserved.

## Latest detached v12-true preflight

- [`experiments/tb2-argus-v12-true-20260516T005644Z/`](/home/argustest/argus-skill/experiments/tb2-argus-v12-true-20260516T005644Z/)
- The tracked bundle is `launch_failed`, not a completed comparison run.
- Its `status.json` records a Docker Hub rate-limit failure for
  `alexgshaw/adaptive-rejection-sampler:20251031` before any comparison trial
  was burned.
- The bundle’s `preflight.json` is the durable record of the image list and
  preflight failure details.

## Interpretation

Use the archived bundles for paper claims about reward, cost, wall time, and
manual-attention accounting. Use the healthy smoke only as evidence that the
launcher/auth fix works. Do not cite the `211403Z` detached bundle as a clean
fullbench result until it has a completed `status.json` and a transcript free of
`302 Found`/`401 Unauthorized` auth failures.
Do not cite the `005644Z` detached bundle as a fullbench result either; it
failed in preflight with a Docker Hub rate-limit failure and never launched a
comparison trial.
