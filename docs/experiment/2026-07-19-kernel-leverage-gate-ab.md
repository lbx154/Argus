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

## Live B200 confirmation

The modified vertical was then checked live on one NVIDIA B200 against the
equal-head `chunk_kda` fwdbwd path (`B=8, T=1024, H=8, D=64`, bf16,
`FLA_TILELANG=1`, `FLA_FLASH_KDA=0`). Dispatch logging proved that
`kda.chunk_kda_bwd_wy_dqkg_fused` used TileLang.

The project-native warmed benchmark reported an end-to-end median of
`1.849456 ms` with p20/p80 of `1.832134/1.863795 ms`. A five-iteration Torch
CUDA timeline measured the target `kernel_kernel` launch at
`0.110912 ms` median (`0.110495–0.111232 ms`), or `5.9970%` of end-to-end time.
Using the same historically observed plausible kernel gain (`1.144057x`), the
live gate computed:

- predicted total speedup: `1.007609x`;
- kernel speedup required to clear `1.02x`: `1.485797x`;
- theoretical total speedup if the target vanished: `1.063796x`;
- verdict: `reject_insufficient_plausible_gain`.

This is the live B result: it reaches the historical no-go decision before a
source edit, candidate correctness run, candidate benchmark, or candidate
counter profile. The machine-readable summary is
`docs/experiment/artifacts/2026-07-19-live-b200-leverage.json`.

For cross-checking only, a filtered three-section NCU collection required nine
passes and reported `0.17136 ms` for the same launch, about 54% above the
low-overhead timeline duration. NVIDIA documents that metric count, selected
sections, replay passes, and memory save/restore can add profiling overhead.
The experiment therefore tightened the vertical: leverage uses timeline time;
multi-pass NCU is a post-gate mechanism diagnostic, not a share estimator.

The first NCU launch also failed before kernel execution because `/tmp` was the
script directory and the project root was absent from `PYTHONPATH`. It succeeded
unchanged after repairing the import path. This is recorded as an environment
failure, not an idea failure. The initial benchmark additionally spent several
minutes in JIT/runner setup and PVC/NFS reads before steady-state GPU execution;
that bootstrap time is reported separately and excluded from kernel latency.

## Limitations

The A arm is an observed historical workflow and the B arm is a deterministic
replay plus a live B200 confirmation, not a randomized multi-agent trial. The
claim is process-bounded: this gate avoids a known low-leverage branch; it does
not prove that every future kernel target will consume less total search time.
