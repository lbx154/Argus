---
name: "Model-Selection Loop Skill (Engineer)"
description: "Three-intent loop guide for the engineer when Argus autonomously selects (and creates) a forecasting model for the quant_factor domain — pick/create candidates from the model space, evaluate the nested walk-forward scoreboard, and decide continue / stop / expand. Inject during the run stage so model choice stays disciplined and every trial is ledgered."
---

## Title
Model-Selection Loop Skill (Engineer)

## Description
Use this skill while the active domain is `quant_factor`, the feature library is
already built (Alpha360 [+ fundamentals] via `integrations/qlib_cn/features.py`),
and the task is to choose the MODEL that turns those features into a signal. It is
the model-layer sibling of the factor-mining loop: same discipline (ledgered
trials, OOS quarantine, honest multiple-testing haircut), different object — you
are choosing/creating a model, not a factor.

**Why a loop and not one model:** there is no universally best model. The right
choice is task-conditional (tabular + low SNR → trees; more data/features → nets)
and can drift. Selecting well without fooling yourself is the whole game — and
model search is a *larger* overfitting surface than factor search, so the
discipline below is not optional.

## The model space (creating a model = emitting a config)
Candidates are `ModelSpec(family, config)` from `model_toolkit/registry.py`.
Emitting a new spec IS creating a new model/architecture:
- **L1 — pick a family**: `gbdt` (LightGBM, NaN-native tabular default),
  `mlp` (torch feed-forward), `linear` (ridge baseline).
- **L2 — pick an architecture** (config-level, no code): MLP `hidden_dims` /
  `dropout` / `lr`; GBDT `num_leaves` / `learning_rate` / regularisation.
- **L3 — author a new architecture** (code): only when L1/L2 are exhausted and
  the evidence says capacity is the bottleneck. Gate it; it is expensive and
  unstable, and rarely the winning lever versus better features.

## The three intents
On every round do exactly one of the three. Never improvise a fourth.

### 1. `select_models(space, task_profile, prior, prior_history)`
Pick a subset of candidates to evaluate this round (or propose a new config).
- **Respect the task prior** (`task_profile.prior_for_profile`): try high-prior
  families first, but always keep a **cheap baseline** (ridge) — the winner must
  beat it after costs or it is not real.
- **Pick with intent**: each candidate must add a distinct hypothesis (a family
  or a capacity level), not a near-duplicate config. Duplicates waste effective
  trials and inflate the haircut.
- **First round**: state, before any number, WHY the prior orders things this way
  for this task (data size, feature families, SNR).

### 2. `evaluate_selection(selection_result)`
Critique the nested walk-forward scoreboard.
- Cite the **exact** per-candidate median fold rank-IC, ICIR, and per-fold ICs —
  never paraphrase.
- Judge on the **robust** metric (median across folds), not a lucky best fold.
  A candidate that is great on one fold and negative on others is **unstable →
  reject**, even if its mean is high.
- Flag **suspicious** wins: a deep model beating trees by a hair, high in-fold IC
  that collapses across folds, or a winner whose edge is smaller than the
  **effective-trials** deflation would erase. `IC` far above the field is more
  likely leakage/overfit than skill.

### 3. `decide_next(history, latest_eval, space)`
Choose exactly one:
- `continue` — try more configs (e.g., tune the winner's architecture at L2).
- `stop` — converged: the winner is robust across folds AND its OOS Sharpe
  survives the haircut by the **effective** number of trials.
- `expand_space` — a needed hypothesis is missing (a family/architecture, or —
  gated — an L3 novel architecture).

## Non-negotiable discipline
- **Nested walk-forward only** for selection: train on a fold's past, score on its
  future; the final OOS/test window is **quarantined** and touched once, by the
  winner, after selection.
- **Every candidate × fold trial is ledgered** (`select_model` does this) before
  the winner is known — cherry-picking is visible in the ledger.
- **Deflate by EFFECTIVE trials** (`analysis.multiple_testing.effective_num_trials`),
  not the raw count — correlated candidates are ~one look. Report raw vs deflated.
- **Prefer the simpler model** unless the complex one clears deflation by a margin.
- **Adapt over time** (concept drift) with rolling retrain / DDG-DA — treat each as
  one more disciplined candidate, compared against strong simple baselines.

The reviewer reads the ledger + report and certifies whether the search was broad,
the OOS split honoured, and the winner chosen for robustness — not curve-fit luck.
