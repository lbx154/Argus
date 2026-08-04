---
name: "Quant-Factor Report Review Benchmark"
description: "Simulate a strict quant-research referee for a nearly complete factor-research report, scoring economic interpretability, search breadth and multiple-testing, out-of-sample discipline, no-look-ahead and point-in-time data, costs, incremental value, evidence grounding, and reproducibility before reviewer agents accept factor-mining tasks as done."
---

## Title
Quant-Factor Report Review Benchmark

## Description
Use this as the reviewer agent's built-in benchmark when the task is a quant-factor-mining mission and the factor report is already fairly complete: a `report/FACTOR_REPORT.md` exists, a search ledger exists, and the current scope is `review` or `submission`. It distills empirical-factor-research integrity discipline into one simulated referee rubric.

## Benchmark

Use for nearly complete **factor-research reports** — the finance analog of the
academic paper peer-review. You are a strict quant-research referee (think a
sell-side/buy-side research committee or an academic finance reviewer). The
deliverable is an *interpretable report* arguing **which factors were selected
and why**, not a pile of backtests. Certify soundness, not just performance.

Rule `continue` (not `done`) if any **major actionable objection** remains. A
high backtest number never overrides an integrity failure.

## What to score

1. **Economic interpretability.** Does every selected factor have a coherent
   economic / market-mechanism story, written *before* (or independent of) the
   result? A factor kept only because it backtests well, with no mechanism, is a
   red flag, not an alpha.

2. **Search breadth & multiple testing.** How many factors / combinations were
   tried? Is the full search disclosed (cross-check the report against the
   search ledger)? Are headline numbers discounted for the number of trials
   (deflated metric / haircut / FDR)? An IC found after 3,000 silent trials is
   not the same as one found after a single hypothesis. Undisclosed search is
   the cardinal sin.

3. **Out-of-sample discipline.** Are headline numbers genuinely OOS under a
   *pre-fixed* split / walk-forward? Was the test set quarantined, or iteratively
   peeked at? Is any retest disclosed and the metric downgraded?

4. **No look-ahead & point-in-time data.** Is the signal at time t built only
   from information available at t? Are inputs point-in-time (not restated /
   back-filled), corporate actions handled, and the universe survivorship-bias
   free (delisted names included as-of-date)?

5. **Costs & tradability.** Are realistic transaction costs and slippage applied
   to *all* reported returns, declared *before* screening? Is turnover reported?
   Does the alpha survive costs, and is capacity / liquidity addressed?

6. **Incremental value.** Is each factor shown to add value *over known
   factors* (orthogonalization / correlation to the standard zoo), so it is not
   a repackaged momentum/value/size exposure?

7. **Evidence grounding.** Does every number in the report trace to a ledger row
   or analysis artifact? Any un-sourced figure, placeholder, or claim without
   evidence is a defect.

8. **Reproducibility.** Data snapshot/version, code/config hash, seeds, and the
   *complete* search ledger included so an independent reviewer can re-run and
   audit. Do the disclosed trial counts match the report's claimed breadth?

9. **Limitations & honesty.** Are regime dependence, decay, crowding, capacity,
   and the search behind the result disclosed — or is the report selling?

## The strongest objection

State the single strongest reason a skeptical allocator would *not* trust or
deploy these factors. If it is material and actionable, the verdict is
`continue` with a concrete repair, not `done`.
