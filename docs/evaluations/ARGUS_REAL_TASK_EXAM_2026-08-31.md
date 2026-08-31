# Argus real-task exam — 2026-08-31

## Baseline

- Final public base: `c1132854`; final private base before synchronization: `81a46d3b`.
- Rebased fixes: `34a8b809`, `f676cdff`, `42d63d50`, `a764a37b`.
- Backend: Pi + GitHub Copilot, `gpt-5.6-sol` for Manager, Planner, Engineer, and Reviewer.
- GitHub HTTPS interruptions delayed synchronization; after recovery, the fixes were rebased without conflict onto the latest public main. No force push was attempted.

## Real trials

| Trial | Result | Evidence |
|---|---|---|
| Kernel optimization | PASS | One real Argus run, 2/2 fixed tests, independent repeated benchmark speedup 77.8×–83.6×, random shapes/radii matched the reference. |
| Compact paper production | PASS with reporting caveat | Final run `s-4bbc8598`: 147 s, one mission, Reviewer `done`, deterministic Manager `complete`, project done, natural daemon exit; six flat deliverables; independently recomputed means 71.280/72.140, paired difference 0.860, SD 0.416, SE 0.186, 95% interval [0.344, 1.376]. No LaTeX engine was available. Manager/report prose incorrectly called the Reviewer's reproducibility rerun “no post-result rerun”; numerical claims were unaffected. |
| Finance/sales/ads pack | PARTIAL FAIL | Run `s-ebdf62a1`: 259 s, one mission, natural exit. Decimal reconciliation and verifier passed: USD net 3900.25, spend 1600.00, ROAS 2.44; CNY net 9798.00 and ROAS N/A; all campaign metrics matched. SVG was readable. PPTX fallback was wrong because `/usr/bin/python3` could import `python-pptx 1.0.2`, while Engineer/Reviewer probed only the framework Python environment. |
| Skill/Wiki A→B | PASS for Skill; UNTESTED for Wiki-page reuse | A `s-d19f3c10`: 156 s, one mission, one reusable shallow Skill, Wiki initialized but Agent correctly wrote no redundant page. B `s-fb885bee`: 148 s, new session and disjoint role threads; full I/O recorded two reads of A's Skill and two reads of `REPORT_A.md`; output correctly preserved zero vs blank and USD/EUR/CNY; no A file or Skill was modified. No Wiki page existed, so cross-task Wiki-page reuse was not tested. |

## Fixes from real failures

1. Reviewer `done` and technical `plan_signal` advice no longer force another Manager semantic vote.
2. Bounded direct nonterminal stages complete instead of advancing through an inapplicable staged campaign.
3. A valid direct completion certificate closes bounded research without requiring a staged final-submission journal.
4. Direct completion no longer requires staged artifact bundles; staged and open-ended gates remain.
5. Unversioned direct work no longer emits schema-invalid `life.plan.revision.rejected` events with version 0.
6. A bounded Manager-direct mission receipt immediately reflects the Manager `complete` verdict.

No domain-specific paper/learning branch, new retry, default target, hash, ID, dependency, or framework layer was added.

## Remaining verified issues

- PPTX capability discovery can miss tools installed under another existing interpreter.
- Manager completion reporting can overstate execution-history claims.
- Provider-reported token usage was anomalous: B used fewer calls than A (4 vs 6) but more input tokens and cost, almost entirely in a 3,448-character no-tool Manager completion prompt reported as 283,438 input tokens. Skill reuse reduced rounds, but token savings were not demonstrated.
- `life.mission.completed` in runs before `a764a37b` said `campaign_continues=true` immediately before project completion; `a764a37b` fixes this projection for Manager-direct bounded work.

## Verification

Focused suites passed throughout, including 563 direct/staged tests, 58 revision/schema tests, and all 800 `tests/life` tests. On the rebased latest public main, full pytest reported 6934 passed, 28 skipped, and 4 failures in newly merged public subagent/PPT code outside every local changed path. Event-type generation passed. Ruff passed on all changed Python files; full-repository Ruff still reported errors in newly merged public files outside this change set.
