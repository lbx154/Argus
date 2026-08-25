# Continuous frontier-search protocol

## Purpose

Keep curiosity connected to the current public frontier. Search proactively whenever
new external mechanisms may expand the plan; do not require a failure, route change,
stage transition, or timer trigger. Independent mechanism families may be researched
in parallel. A decision-useful report is a valid result even without implementation.

## Required search surfaces

1. **Target repository:** latest main, open/merged PRs, issues, releases,
   benchmark changes, maintainer comments, and CI/version matrix. Search first
   to avoid duplicating active upstream work.
2. **Official toolchains:** release notes and current docs for the selected GPU,
   PyTorch, CUDA/ROCm, Triton/Gluon, TileLang, CUTLASS/CuTe, vendor libraries,
   profilers, and relevant specialist packages.
3. **Research frontier:** recent arXiv/OpenReview papers and author-maintained
   code for the exact operator, adjacent mechanisms, target hardware, and
   benchmark. Sort or filter by recent submission/update date.
4. **Adjacent implementations:** current specialist libraries, benchmark suites,
   serving/training stacks, and public optimized kernels that expose a stronger
   baseline or transferable mechanism.

Use broad search engines only for discovery. Bind decisions to primary sources:
official repositories/docs/releases, PRs/issues, paper/preprint pages, author
repositories, or standards. Record secondary sources only as discovery aids.

## Query construction

- Search exact op names plus synonyms, model families, shapes/dtypes, and target
  architecture (`B200`, `Blackwell`, `sm_100`, etc.).
- Search the intended implementation language and alternatives: Triton,
  TileLang, CUTLASS/CuTe, CUDA C++, vendor primitives, communication stack.
- Search failure text exactly when blocked; compiler/runtime errors often map to
  known version or architecture issues.
- Search open PRs/issues before coding and immediately before preparing a PR.
- Use recent windows (30/90/365 days) but retain older canonical mechanisms.

## Evidence artifact

When a durable report is useful, create a fresh snapshot at
`research/frontier/<stage>.json` and append it with the provided recorder to
`research/FRONTIER_WATCH.jsonl`. A stage transition alone does not require a new
snapshot. The JSONL file is append-only audit output; never load it in full.

```bash
python -m argus_skill.verticals.kernel_engineering.frontier_watch template \
  --stage optimize > /tmp/frontier-optimize.json
# Replace placeholders using real online research.
python -m argus_skill.verticals.kernel_engineering.frontier_watch record \
  --project-root . --stage optimize --input /tmp/frontier-optimize.json
python -m argus_skill.verticals.kernel_engineering.frontier_watch check \
  --project-root . --stage optimize
```

Each snapshot may contain broad or focused queries, checked surfaces, sourced facts,
speculative hypotheses, mechanism comparisons, and open questions. It need not end in
an action, implementation, or immediately verifiable claim. Reviewer judgment, not
fixed query/source counts, decides whether the exploration is useful.
`frontier_watch check` validates both the current snapshot and its latest
same-stage ledger record, so agents and reviewers do not need to read the ledger.

## Decision discipline

- New work does not automatically invalidate measured local evidence. Reproduce
  relevant public results under the project's contract before adopting claims.
- A new package/release can change environment requirements; refresh the
  environment audit before using it.
- A new upstream PR may make local work duplicative; coordinate, change scope,
  or build on it rather than racing blindly.
- No material update is a valid result when the search is real and documented.
- Offline/no-network status is a freshness blocker. Continue local diagnostics
  if useful, but do not certify the stage or claim the plan is current.
