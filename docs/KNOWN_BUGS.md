# Known Bugs

This file tracks known deviations that are still present in checked-in
artifacts but are intentionally preserved for provenance.

## Legacy benchmark summaries

`benchmarks/results/` remains ignored generated output, not source.
Checked-in evidence bundles live under `benchmarks/evidence/`. Keep
archival summaries in `benchmarks/reports/` or `docs/`; do not check
whole scratch result trees into this repository.

There are currently no checked-in exempt result bundles. If a bundle is
intentionally promoted from local scratch to source control, backfill the
protocol files or add `EXEMPT.md` and document the exception here in the
same change.

## Detached TB2 evidence gap

The detached argus bundle at
[`experiments/tb2-argus-v12-redux-20260515T211403Z/`](/home/argustest/argus-skill/experiments/tb2-argus-v12-redux-20260515T211403Z/)
remains `running`. Its job transcripts under
[`jobs/2026-05-15__21-14-04/.../agent/codex.txt`](/home/argustest/argus-skill/experiments/tb2-argus-v12-redux-20260515T211403Z/jobs/2026-05-15__21-14-04/git-multibranch__Jgja2U8/agent/codex.txt)
and sibling rounds show repeated `302 Found` websocket redirects followed by
`401 Unauthorized` API failures against
`https://ai4m6.openai.azure.com/openai/v1/responses`.

Do not cite that bundle as clean performance evidence. Use the archived bundles
under `benchmarks/evidence/` for reward/cost/wall-time claims, and use the
completed smoke at
[`experiments/tb2-harbor-smoke-20260515T235537Z/`](/home/argustest/argus-skill/experiments/tb2-harbor-smoke-20260515T235537Z/)
as the positive auth/control proof.

The verifier-gated detached run at
[`experiments/tb2-argus-v12-true-20260516T005644Z/`](/home/argustest/argus-skill/experiments/tb2-argus-v12-true-20260516T005644Z/)
is also not clean evidence. It failed in launcher preflight with a Docker Hub
rate-limit message for `alexgshaw/adaptive-rejection-sampler:20251031`, so it
records a `launch_failed` control-path result rather than a burned fullbench.
