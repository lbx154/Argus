# Erdős--Gyárfás vertical trace snapshot

This directory freezes one representative mathematical-research trajectory for
the Argus technical report. The source campaign is session `s-9509b268` with
workdir `/home/argustest/argus-tests/erdos-capability`.

## Freeze and inclusion rule

- Freeze time: 2026-07-18 08:23 UTC.
- Included theorem nodes: `C20` through `C25` from
  `research/CLAIM_LEDGER.md`.
- Included negative node: `C5`, the Reviewer-accepted falsification of the first
  induced-path reduction.
- A node is marked `reviewer_verified` only when the campaign record reports
  correctness accepted by the Reviewer. This does not imply external novelty,
  publication readiness, or resolution of the Erdős--Gyárfás conjecture.
- Computation is represented only as falsification or boundary-audit evidence;
  universal claims are attributed to the corresponding proof artifacts.
- The efficiency audit covers theorem-production Rounds 12--17. It attributes
  whole missions and role calls by primary purpose; it does not label private
  chain-of-thought or claim token-level semantic precision.
- The efficiency telemetry cutoff is 2026-07-18 08:41 UTC, after the 08:23 claim
  snapshot because it includes Round 17's post-proof review-state closure. No
  theorem beyond C25 is included in the efficiency window.

## Source artifacts

- `/home/argustest/argus-tests/erdos-capability/research/CLAIM_LEDGER.md`
- `/home/argustest/argus-tests/erdos-capability/research/LEMMA_GRAPH.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND12_THEOREM_FIRST.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND13_PATH_EXCLUSION.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND14_LENGTH4_PATH_DICHOTOMY.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND15_BLOCKER_BOUND.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND16_GRAPH_REALIZABILITY.md`
- `/home/argustest/argus-tests/erdos-capability/research/SOLVE_ROUND17_FANO_REALIZABILITY.md`
- `/home/argustest/.argus-skill/projects/s-9509b268/events.jsonl`
- `/home/argustest/.argus-skill/projects/s-9509b268/events.jsonl.1`
- `/home/argustest/.argus-skill/projects/s-9509b268/events.jsonl.3`

`efficiency_audit.csv` freezes aggregates from `life.mission.completed` and
`usage.recorded` events used by the report. The
editable figure source is `technical_report/figures/erdos_agent_trace.html`.
