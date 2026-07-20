# Continuous frontier-search protocol

## Purpose

Keep every stage grounded in the current public frontier. Online research is a
decision input, not a one-time literature stage. Search at stage entry, refresh
after repeated mechanism failures or before a route change, and refresh again
before an upstream PR/report. Refresh is event-driven, not timer-driven.

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

Create one fresh snapshot per stage at `research/frontier/<stage>.json` and
append it with the provided recorder to `research/FRONTIER_WATCH.jsonl`.

```bash
python -m argus_skill.verticals.kernel_engineering.frontier_watch template \
  --stage optimize > /tmp/frontier-optimize.json
# Replace placeholders using real online research.
python -m argus_skill.verticals.kernel_engineering.frontier_watch record \
  --project-root . --stage optimize --input /tmp/frontier-optimize.json
python -m argus_skill.verticals.kernel_engineering.frontier_watch check \
  --project-root . --stage optimize
```

Each snapshot must contain concise focused queries, checked surfaces, sources
that support the decision, material findings and actions, or an explicit
`no_material_update=true` with a decision-impact explanation. Reviewer judgment,
not fixed query/source counts, decides whether the evidence is sufficient.

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
