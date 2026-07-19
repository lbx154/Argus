# Kernel vertical A/B: Amdahl leverage gate

Date: 2026-07-19 UTC

## Question

Can the modified kernel-engineering vertical reach the same correct no-go
decision with less GPU/agent work than the previous vertical?

## Fixed evidence

The replay uses the completed Argus3 B200 attempt
`tilelang-bwd-dqkg-profile-20260718T145000Z`:

- project-native end-to-end fwdbwd median: `1.809264 ms`;
- selected generated TileLang kernel: `0.167490 ms`;
- candidate kernel duration: `0.146400 ms` (`1.1441x` kernel speedup);
- candidate end-to-end median: `1.814224 ms` versus the pre-edit current
  `1.809264 ms`;
- final reviewed verdict: no-go / not retained.

The required end-to-end improvement is `1.02x`, chosen to clear the observed
microbenchmark spread rather than reward a sub-percent fluctuation.

## A — previous vertical (observed)

The previous workflow continued after the baseline profile:

1. edited TileLang `num_warps`;
2. ran targeted correctness twice;
3. ran candidate end-to-end timing;
4. collected a second NCU report;
5. wrote and reviewed the no-go outcome.

Baseline NCU evidence was complete at 15:02:19 UTC. The final outcome was
written at 15:27:57 UTC, so the post-profile candidate branch consumed about
25 minutes 38 seconds before reaching no-go.

## B — modified vertical (deterministic replay)

The new `leverage_gate` recomputes:

- target share of end-to-end time: `0.09257`;
- theoretical total speedup if the kernel vanished: `1.1020x`;
- plausible total speedup from the measured/expected `1.1441x` kernel gain:
  `1.01179x`;
- kernel speedup required to clear `1.02x` end-to-end: `1.2687x`;
- verdict: `reject_insufficient_plausible_gain`.

The B workflow therefore stops after the first profile, records the target as a
no-go, and chooses a higher-leverage boundary. It reaches the same retained-code
decision without the candidate edit, correctness reruns, candidate benchmark,
or second NCU collection.

## Outcome

| Metric | A: previous | B: leverage gate |
|---|---:|---:|
| Final decision | no-go | no-go |
| Source edit required | yes | no |
| Candidate correctness runs | 2 | 0 |
| Candidate benchmark | yes | no |
| Candidate NCU profile | yes | no |
| Post-profile decision latency | ~25m38s | <1s calculation |
| Fixed prompt delta | baseline | +279 Engineer / +59 Reviewer estimated tokens |

The leverage gate trades a small fixed prompt/schema cost for eliminating an
entire low-leverage candidate branch. Raw profiler evidence remains on disk and
continuation prompts remain compact.

## Limitations

This first A/B is a deterministic historical replay, not a randomized agent
trial. A live B200 check should run the same path-aligned baseline microbenchmark
and feed its measured wall-clock/kernel times to the new gate; it should not
apply the rejected candidate when the gate remains red.
