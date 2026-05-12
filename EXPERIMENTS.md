# Experiments index

A single-file index of every benchmark / ablation run on this machine.
Append a new row whenever you start a new experiment so future searches
can grep this file instead of trawling timestamps.

**Hard rule**: experiments live under
`benchmarks/results/<bench>-<scope>-<YYYY-MM-DD>[-vN]/` (see naming
convention in any `PLAN.md`). **Never `/tmp`.**

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
