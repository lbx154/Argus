# Experiments index

A single-file index of every benchmark / ablation run on this machine.
Append a new row whenever you start a new experiment so future searches
can grep this file instead of trawling timestamps.

**Hard rule**: checked-in archive bundles live under
`benchmarks/evidence/<bench>-<scope>-<YYYY-MM-DD>[-vN]/`; ignored scratch
runs stay under `benchmarks/results/` or `benchmarks/prompt_only_tb2/runs/`
(see naming convention in any `PLAN.md`). **Never `/tmp`.**

## Index

| Date | Dir | Bench | Status | Headline |
|---|---|---|---|---|
| 2026-05-01 | `benchmarks/results/tb2-bare-large-2026-05-01/` | TB v2 fullbench (89 tasks) — **bare-large baseline** (archived from `/tmp/` 2026-05-10, source `skill-agent` era) | done | codex 纯 gpt-5.4/high; reward **0.6629** (59 pass), $29.08, **$0.327/trial**, dataset commit `69671fbaac6d…`, cache hit 91.8% — see `RESULTS.md` |
| 2026-05-02 | `benchmarks/results/tb2-bare-mini-2026-05-02/` | TB v2 fullbench (89 tasks) — **bare-mini baseline** (archived from `~/skill-agent/` 2026-05-10) | done | codex 纯 gpt-5.4-mini/high; reward **0.5618** (50 pass), $12.21, **$0.137/trial**, cache hit 97.2% — see `RESULTS.md` |
| 2026-05-02 | `benchmarks/results/tb2-microbench-2026-05-04/` | TB v2 microbench | done | early baseline (skill-agent era) |
| 2026-05-06 | `benchmarks/results/tb2-microbench-2026-05-06-v8..v11/` | TB v2 microbench | done | iteration on reviewer-gate / R2 trigger |
| 2026-05-06 | `benchmarks/results/tb2-fullbench-2026-05-06-v12/` | TB v2 fullbench (89 tasks) | done | reward **0.5955** (53 pass), **$12.35 total / $0.139 per trial** (修正：之前算成 $18.79 是把 658 个 host session 都算进去了，实际过滤到 182 个 v12-window session) — 详见 `docs/RETROSPECTIVE-v12-vs-current.md` 和 `benchmarks/reports/2026-05-10-tb2-baseline-vs-v12.md` |
| 2026-05-06 | `benchmarks/results/swebench_pro_smoke/`, `..._verifier_smoke/`, `..._verifier_v2/` | SWE-Bench-Pro smoke | done | adapter shakedown |
| 2026-05-06 | `benchmarks/results/swebpro-pilot-2026-05-06/` | SWE-Bench-Pro pilot | done | precursor to 05-07 pilot55 |
| 2026-05-07 | `benchmarks/results/swebpro-codex-baseline-2026-05-07/` | SWE-Bench-Pro 55-task pilot — codex-bare control | done | reward 0.164 (9/55), $140.20 |
| 2026-05-07 | `benchmarks/results/swebpro-argus-pilot2-2026-05-07/` | SWE-Bench-Pro 55-task pilot — argus-skill | done | reward 0.600 (33/55), $95.42 — see `benchmarks/reports/2026-05-07-pilot55.md` |
| 2026-05-09 | (was at `/tmp/ablation`) → `benchmarks/results/ablation-2026-05-09/` | toy "Build Typed Python Package" ablation | **mis-located** | 5 conditions × 1 round all pass; cost=$0.0 (telemetry bug); reviewer/gate fact-check inconsistent — needs rerun |
| 2026-05-09 | (was at `/tmp/ablation_p2`) → `benchmarks/results/ablation-2026-05-09-p2/` | toy "Typed Python Utility (ttlcache)" ablation | **mis-located** | 4 conditions × 1 round; pytest_cov + mypy gates inconsistent with reviewer "done" — reviewer hallucination evidence |
| 2026-05-10 | `benchmarks/results/tb2-ablation-2026-05-10/` | TB v2 single-task ablation (`fix-git`) | done • bugs fixed | n=1: A0_bare 1/1 (77 s), A1_reviewer **0/1** (302 s), A2_full 1/1 (247 s). Surfaced 2 bugs (now fixed): reviewer 60 s budget too tight (default→180 s) + `harbor_adapter.py` `.render()` on `None`-from-`save_distilled` (gate-reject branch added). See `RESULTS.md` §6. |
| 2026-05-10 | `benchmarks/results/tb2-ablation-2026-05-10-v2/` | TB v2 single-task ablation (`fix-git`) — **rerun post-fixes** | done | Re-ran same 3 conditions with reviewer budget=180 s and `save_distilled` None-branch fix. 3/3 PASS; F1 + F2 fixes confirmed; surfaced F4 (harbor multi-round token undercount) and F5 (8-15× cost overhead vs reward parity). See `RESULTS.md`. |
| 2026-05-10 | `benchmarks/results/tb2-ablation-2026-05-10-v3/` | TB v2 efficiency ablation (`fix-git` + `git-leak-recovery`) | done | 4 cond × 2 tasks × n=1 (driver bug F6, plan was n=2). 8/8 PASS reward=1.0. C2_lean_full cost=2.57× C0 → **H1 REJECTED**. Surfaces 3 architectural root causes (RC1 reviewer can't see verifier; RC2 R2 prompt = R1 entropy; RC3 swebpro headline confounds 4 vars). v4 priority: reviewer-sees-verifier prototype. |
| 2026-05-10 | `benchmarks/results/tb2-ablation-2026-05-10-v4-proto/` | RC1 prototype: reviewer-sees-acceptance-checks (no TB run) | done | Code change: `_invoke_reviewer` no longer hardcodes `checks=[]`; new `_collect_checks` runs user-configured commands inside container after each round; new `ARGUS_SKILL_HARBOR_CHECKS_CMD` env var. 8 unit tests added (24/24 pass). Smoke (5×3=15 mini@low calls): **2/5 cases flip continue→done when checks=PASS injected**, 0/5 false-flips, 5/5 stay continue when checks=FAIL. RC1 fix architecturally validated; reward-lift quantification deferred to v4-pri-2 ablation. |
| 2026-05-10 | `benchmarks/results/tb2-ablation-2026-05-10-v4-pri2/` | TB v2 oracle ablation: reviewer-sees-checks vs blind (n=1) | done | 2 cond × 2 tasks × n=1 (4 trials, oracle mode). C0_blind reward μ=0.5 wall μ=174s cost μ=$0.058; C1_sees reward μ=1.0 wall μ=94s cost μ=$0.025 → **+0.5 reward, −46% wall, −57% cost, −25% reviewer calls**. Mechanism confirmed: git-leak-recovery R1 checks PASS → reviewer flips done at R1 (1 round vs 2 in C0). All 4 hypotheses (H1 architecture-uses-signal, H2 faster, H3 reward, H4 no-false-done) ✓. Surfaced 2 bugs: B1 (`python3` missing in ubuntu:24.04 git-leak container — fixed via apt fallback prefix), B2 (`output_tail` empty on all checks — `passed`/`exit_code` correct, deferred to v4-pri-3). Caveats: n=1 (reward delta suggestive only), oracle mode (reviewer sees the literal verifier). See `RESULTS.md`. |
| 2026-05-15 | `benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z/` | TB v2 prompt-only smoke bundle | done | 12-trial smoke export with bundle-local jobs/index, summary.tsv, PLAN/BUILD_INFO/RESULTS, and preserved verifier logs; current archive-root contract example |
| 2026-05-15 | `experiments/tb2-bare-gpt54-20260515T201322Z/` → `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/` | TB v2 fullbench comparison — bare gpt-5.4 | done • archived | detached launch wrote `manifest.json` / `pid` / `stdout.log` / `stderr.log` / `status.json`; archived bundle preserves aggregate and trial-level reward/wall/error annotations |
| 2026-05-15 | `experiments/tb2-bare-gpt54-mini-20260515T201322Z/` → `benchmarks/evidence/tb2-bare-gpt54-mini-20260515T201322Z/` | TB v2 fullbench comparison — bare gpt-5.4-mini | done • archived | detached launch wrote `manifest.json` / `pid` / `stdout.log` / `stderr.log` / `status.json`; archived bundle preserves aggregate and trial-level reward/wall/error annotations |
| 2026-05-15 | `experiments/tb2-argus-v12-redux-20260515T201322Z/` → `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/` | TB v2 fullbench comparison — argus v12-redux | done • archived | detached launch wrote `manifest.json` / `pid` / `stdout.log` / `stderr.log` / `status.json`; archived bundle records explicit missing-cause fields for absent token/cost totals |
| 2026-05-16 | [`experiments/tb2-argus-v12-true-20260516T005644Z/`](/home/argustest/argus-skill/experiments/tb2-argus-v12-true-20260516T005644Z/) | TB v2 fullbench comparison — argus v12-true | launch_failed • tracked | detached launch stopped in preflight before burning trials; `status.json` records Docker Hub rate-limit failure for `alexgshaw/adaptive-rejection-sampler:20251031`, `preflight.json` preserves the checked image list, and this run should not be used as fullbench evidence |
| 2026-05-16 | `experiments/tb2-argus-v12-true-20260516T023000Z/` → `benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z/` | TB v2 fullbench comparison — argus v12-true | completed • archived • residual failures | detached run completed 89/89 trials with `reward=0.011236`, `wall_minutes=6.16`, `n_errored_trials=80`, and `cost_usd=3.55539`; the archived bundle keeps `docker_compose_failure` and the 80 Docker Hub rate-limit trial logs visible, so this is the newest detached v12-true state but not a clean success claim |
| 2026-05-16 | `experiments/tb2-sweep-argus-v12-true-bare-gpt54-rep03/` | TB v2 fullbench sweep — argus v12-true + bare gpt-5.4 | running | matrix launcher started 2 conditions × 3 replicates with deterministic run IDs and per-replicate `manifest.json` / `status.json` / `pid` / stdout / stderr artifacts; see `launch-summary.json` |
| 2026-05-15 | `benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/` | TB v2 prompt-only argus corrected condition | done | verifier-gated prompt-only row for `cancel-async-tasks`; `ARGUS_SKILL_BENCHMARK_VERIFIER_GATE=1`, `zero_touch_success=True`, `human_interactions_after_assignment=0` |
| 2026-05-15 | `benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/` | TB v2 reviewer-gate contrast bundle | done | archived contrast between the reviewer-off self-satisfaction shortcut and the verifier-gated fix; bundle-local `jobs/index.tsv` and transcript logs resolve in this checkout |
| 2026-05-15 | `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/` | TB v2 reviewer-gate contrast + manual follow-up annotation | done | bundle-local annotation row records `manual_commands=1`, `human_interactions_after_assignment=2`, `active_touch_minutes_after_assignment=6.0`, and `manual_rescue=failed`; see `docs/USER_STUDY_PROTOCOL.md` |

## Quick recall queries

```bash
# Find the most recent run by mtime
find /home/argustest/argus-skill/benchmarks/results -maxdepth 2 -type d -printf '%T@ %p\n' \
  | sort -nr | head -10

# What did 05-10 produce?
ls /home/argustest/argus-skill/benchmarks/results/tb2-ablation-2026-05-10/

# All TB v2 runs ever
ls /home/argustest/argus-skill/benchmarks/results/ | grep ^tb2-
```
