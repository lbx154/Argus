"""Quant-factor research vertical — stage definitions and reviewer checklists.

The finance analog of ``argus_skill.verticals.research.stages``. It reuses the
SAME 8 stage ids as the paper pipeline
(``research → plan → benchmark → run → analysis → draft → review →
submission``) so every domain-agnostic mechanism that keys off stage ids keeps
working unchanged; the *finance* semantics are carried by the per-stage shell
checks, the reviewer checklists, the markdown ``CHECKLIST_ITEMS`` (ported from
the original quant-factor domain's ``checklists.py``), and the ``role_banner``.

The deliverable is an interpretable, reviewer-certified **factor report**, so
``completion_gate`` is ``"full_paper"`` (report certification), exactly like
``research`` — NOT a numeric speedrun metric.

The reviewer checklists name two built-in finance skills:

* ``reviewer/quant-factor-report-review.md`` — the strict quant-research referee
  rubric (economic interpretability, search breadth & multiple-testing, OOS
  discipline, no look-ahead / point-in-time data, costs, incremental value,
  evidence grounding, reproducibility); used on the report-shaped stages.
* ``engineer/quant-factor-loop.md`` — the 3-intent select/evaluate/decide loop
  and the non-negotiable ``BacktestExecutor`` / search-ledger contract; used on
  the run / analysis search stages.

Both skill files live under ``argus_skill/builtin_skills/{reviewer,engineer}/``
and are resolved by path by ``argus_skill.tools.stage_check``.
"""
from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]

# Common check: pipeline state must be valid (includes stage ordering). Shared
# with the research vertical; every vertical needs a pipeline-state file.
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

# The finance referee skill (report-shaped stages) and the engineer loop skill
# (search stages). Only these two finance skills exist; early stages apply the
# referee's relevant integrity dimensions.
_REPORT_REVIEW_SKILL = "reviewer/quant-factor-report-review.md"
_LOOP_SKILL = "engineer/quant-factor-loop.md"


# Stage → code checks (description, shell command). Authored from the artifact
# evidence-hints of the ported checklist items. Lenient by design — the reviewer
# is the real gate; these only confirm the stage's artifacts physically exist.
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "research": [
        _PIPELINE_CHECK,
        ("Factor hypotheses present", "test -s research/FACTOR_HYPOTHESES.json"),
        ("GO/NO-GO decision recorded", "test -s research/GO_NO_GO.json"),
        ("Prior/known factors identified",
         "test -f research/PRIOR_FACTORS.tsv || test -s research/PRIOR_FACTORS.tsv"),
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Data provenance disclosed", "test -s plan/DATA_PROVENANCE.md"),
        ("Eval protocol fixed", "test -s plan/EVAL_PROTOCOL.json"),
        ("Cost model pre-declared", "test -s plan/COST_MODEL.json"),
        ("Metrics & thresholds fixed", "test -s plan/METRICS.json"),
    ],
    "benchmark": [
        _PIPELINE_CHECK,
        ("No-look-ahead / leakage checks recorded", "test -s benchmark/LEAKAGE_CHECKS.md"),
        ("Universe bias audit present", "test -s benchmark/UNIVERSE_AUDIT.md"),
        ("Baseline wired in", "test -s benchmark/BASELINE.json"),
        ("Sanity run reproduces an expected result", "test -s benchmark/SANITY_RUN.md"),
    ],
    "run": [
        _PIPELINE_CHECK,
        ("Search ledger has trials",
         "test -s run/SEARCH_LEDGER.jsonl && head -1 run/SEARCH_LEDGER.jsonl | grep -q ."),
        ("Screen results recorded", "test -s run/SCREEN_RESULTS.tsv"),
        ("Combinations built & recorded", "test -s run/COMBINATIONS.json"),
    ],
    "analysis": [
        _PIPELINE_CHECK,
        ("Factor evidence characterized", "test -s analysis/FACTOR_EVIDENCE.json"),
        ("Multiple-testing discount computed", "test -s analysis/MULTIPLE_TESTING.md"),
        ("Orthogonality / incremental value shown", "test -s analysis/ORTHOGONALITY.tsv"),
    ],
    "draft": [
        _PIPELINE_CHECK,
        ("Factor report drafted", "test -s report/FACTOR_REPORT.md"),
        ("Figures present",
         "ls report/figures/* 2>/dev/null | head -1 | grep -q . "
         "|| grep -q . report/FACTOR_REPORT.md"),
    ],
    "review": [
        _PIPELINE_CHECK,
        ("Factor report present for review", "test -s report/FACTOR_REPORT.md"),
        ("Search ledger disclosed", "test -s run/SEARCH_LEDGER.jsonl"),
    ],
    "submission": [
        _PIPELINE_CHECK,
        ("Reproducibility manifest present", "test -s report/REPRO_MANIFEST.json"),
        ("Final assurance statement present", "test -s report/ASSURANCE.md"),
        # Same ready-or-done acceptance as research: the reviewer flips
        # submission.status ready -> done AFTER this check passes; requiring
        # `done` at check-time would deadlock the verdict.
        ("Submission stage is ready or done", "test -f research/PIPELINE_STATE.json && python3 -c \"import json,sys; d=json.load(open('research/PIPELINE_STATE.json')); st=(d.get('stages') or {}).get('submission') or {}; sys.exit(0 if str(st.get('status','')).lower() in ('ready','done') else 1)\""),
    ],
}


# Stage → reviewer checklist: (skill_to_load, review_instructions, files_to_read)
# The reviewer agent loads the skill, reads the files, and rules itself.
REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "research": (
        _REPORT_REVIEW_SKILL,
        "Evaluate the FACTOR-HYPOTHESES stage (rule on intent stated BEFORE any backtest):\n"
        "1. Economic mechanism — does each hypothesis state a market/economic rationale for why it should predict future returns, with an expected sign, grounded in literature or documented market structure?\n"
        "2. GO/NO-GO predeclared — is an explicit, dated GO/NO-GO decision and expected direction recorded per hypothesis, so a later result cannot retro-justify a factor that had no prior thesis?\n"
        "3. Prior art — are overlapping/known factors (the standard factor zoo) identified up front so novelty and redundancy are understood before testing?\n"
        "Pass: hypotheses are mechanism-grounded with a pre-committed GO/NO-GO, not result-driven fishing.",
        ["research/FACTOR_HYPOTHESES.json", "research/GO_NO_GO.json",
         "research/PRIOR_FACTORS.tsv"],
    ),
    "plan": (
        _REPORT_REVIEW_SKILL,
        "Evaluate the DESIGN stage — the evaluation protocol must be fixed in advance:\n"
        "1. Data provenance & point-in-time — sources, timestamps, revision/PIT handling, corporate-action policy, and universe construction disclosed; inputs as-known-at-decision-time, not restated/back-filled.\n"
        "2. Eval protocol — train/validation/test (or walk-forward windows) fixed in advance, test set quarantined, and the rule for when/how-often it may be touched stated.\n"
        "3. Cost model predeclared — transaction-cost/slippage/turnover assumptions declared BEFORE screening with justification, so costs cannot be tuned after seeing results.\n"
        "4. Metrics predeclared — IC/RankIC, ICIR, long-short, turnover, cost-adjusted return thresholds decided in advance, not chosen after seeing what looks best.\n"
        "BLOCK if the protocol or costs are not pinned before search.",
        ["plan/DATA_PROVENANCE.md", "plan/EVAL_PROTOCOL.json",
         "plan/COST_MODEL.json", "plan/METRICS.json"],
    ),
    "benchmark": (
        _REPORT_REVIEW_SKILL,
        "Evaluate BACKTEST TRUSTWORTHINESS before any broad search:\n"
        "1. No look-ahead — the factor at time t uses only information available at t; future returns t->t+h are aligned without leaking future data into the signal.\n"
        "2. Universe bias-free — survivorship/selection controlled: delisted/dead names included as-of-date, membership reconstructed point-in-time.\n"
        "3. Baseline — at least one baseline (a known factor and/or the market) is wired in so new factors are judged on incremental value, not in a vacuum.\n"
        "4. Harness authenticity — the engine runs end-to-end on a sanity case and reproduces an expected result, proving the substrate is trustworthy.\n"
        "Pass: the backtest substrate is leak-free, bias-controlled, baselined, and sanity-verified.",
        ["benchmark/LEAKAGE_CHECKS.md", "benchmark/UNIVERSE_AUDIT.md",
         "benchmark/BASELINE.json", "benchmark/SANITY_RUN.md"],
    ),
    "run": (
        _LOOP_SKILL,
        "Evaluate the SCREEN & COMBINE search loop — keep it LEAN, the metric is search discipline:\n"
        "1. Search-ledger completeness — EVERY trial (factor, combination, weighting, window, params), including failures and discards, is appended to run/SEARCH_LEDGER.jsonl at execution time through the BacktestExecutor; cherry-picking must be visible.\n"
        "2. Screening — the pre-declared metrics/thresholds were applied; screen criteria and surviving factors recorded.\n"
        "3. Combinations — built explicitly (equal-weight and/or optimized), weighting method and inputs recorded per combination.\n"
        "4. Costs applied — every reported return is net of the pre-declared transaction costs and slippage.\n"
        "EFFICIENCY: trust a clean ledger row; do not re-run a recorded trial. BLOCK if trials bypass the ledger.",
        ["run/SEARCH_LEDGER.jsonl", "run/SCREEN_RESULTS.tsv",
         "run/COMBINATIONS.json"],
    ),
    "analysis": (
        _LOOP_SKILL,
        "Evaluate the EVIDENCE stage — out-of-sample, discounted for search, and TRADABLE:\n"
        "1. Evidence set — each survivor characterized with IC/RankIC, ICIR, quantile monotonicity, long-short return, turnover, cost-adjusted return.\n"
        "2. IC is NOT tradable alpha — a positive RankIC with a flat/negative cost-net long-short (tail non-monotonicity, outlier-driven quintiles) is FLAGGED, not celebrated; the tradable long-short spread decides P&L, not the rank correlation.\n"
        "3. Alpha vs beta, and neutralized — headline alpha is measured BETA-NEUTRAL (dollar-neutral long-short), not long-only (which is market beta); the signal is residualized against size/liquidity/style so a size or momentum factor is not mistaken for novel alpha.\n"
        "4. OOS & drift — headline performance is genuinely OOS under the fixed protocol (test set quarantined, retests disclosed and downgraded), AND confirmed across a WALK-FORWARD of multiple windows rather than one split; a single lucky window is not an edge.\n"
        "5. Multiple testing — data-mining risk quantified from ledger breadth and discounted by the EFFECTIVE number of trials (correlated candidates count as ~one look), via deflated Sharpe / PBO / FDR.\n"
        "6. Independence & de-duplication — collinear factors are de-duplicated before combining, and each selected factor's incremental value over the known factor zoo is shown (orthogonalization / correlation).\n"
        "7. Claims — every quantitative claim is bound to its ledger rows and the figure/table that will show it.\n"
        "BLOCK if OOS/walk-forward discipline, beta-and-size neutralization, or multiple-testing discounting is missing.",
        ["analysis/FACTOR_EVIDENCE.json", "analysis/OOS_REPORT.md",
         "analysis/MULTIPLE_TESTING.md", "analysis/ORTHOGONALITY.tsv",
         "analysis/CLAIM_GRAPH.json"],
    ),
    "draft": (
        _REPORT_REVIEW_SKILL,
        "DRAFT-stage progress check (lenient, not the final referee pass):\n"
        "1. Interpretation-first — for each selected factor/combination, does the report state WHY it was chosen (economic logic + supporting evidence), not merely its performance numbers?\n"
        "2. Limitations disclosed — regime dependence, capacity/liquidity, alpha decay, crowding, and the search breadth behind the result.\n"
        "3. Figures — headline evidence (IC over time, quantile curves, long-short equity, OOS-vs-IS) shown as figures/tables grounded in analysis artifacts.\n"
        "Do NOT block on prose polish. Pass: the report is interpretable and evidence-anchored enough to proceed to review.",
        ["report/FACTOR_REPORT.md"],
    ),
    "review": (
        _REPORT_REVIEW_SKILL,
        "Self-review the report against the quant-research referee rubric:\n"
        "1. Interpretability — every selected factor carries a coherent economic mechanism; none kept purely because it backtests well.\n"
        "2. Evidence grounding — every number traces to a search-ledger row or analysis artifact; no un-sourced figure, placeholder, or claim.\n"
        "3. Search disclosed — the report honestly discloses search breadth (factors/combinations tried) and the multiple-testing adjustment, cross-checked against run/SEARCH_LEDGER.jsonl, so a referee can judge cherry-picking.\n"
        "Block on any material, actionable integrity objection; a high backtest number never overrides an integrity failure.",
        ["report/FACTOR_REPORT.md", "run/SEARCH_LEDGER.jsonl"],
    ),
    "submission": (
        _REPORT_REVIEW_SKILL,
        "FINAL report gate — be STRICT, rule as a skeptical allocator / research committee.\n"
        "All must pass:\n"
        "1. Economic interpretability — every factor has a coherent mechanism written before/independent of the result.\n"
        "2. Search breadth & multiple testing — full search disclosed and headline numbers discounted (deflated/haircut/FDR).\n"
        "3. OOS discipline — headline numbers are genuinely OOS under a pre-fixed split, confirmed across a WALK-FORWARD of multiple windows (not one lucky split); retests disclosed.\n"
        "4. No look-ahead & point-in-time data; survivorship-bias-free universe.\n"
        "5. Costs, tradability & neutrality — realistic costs applied to ALL returns; alpha measured BETA-NEUTRAL (dollar-neutral long-short, NOT long-only market beta) and residualized against size/style so it is not a repackaged size/momentum factor; turnover & capacity addressed.\n"
        "6. Incremental value over the known factor zoo (collinear factors de-duplicated first).\n"
        "7. Evidence grounding — every number sourced.\n"
        "8. Reproducibility — data snapshot/version, code/config hash, seeds, and the COMPLETE search ledger included; disclosed trial counts match the report's claimed breadth.\n"
        "State the single strongest reason a skeptical allocator would NOT deploy these factors; if material and actionable, verdict is continue, not done.",
        ["report/FACTOR_REPORT.md", "report/REPRO_MANIFEST.json",
         "report/ASSURANCE.md", "run/SEARCH_LEDGER.jsonl"],
    ),
}


# ===========================================================================
# System (B) — markdown stage checklists for the quant vertical
# ===========================================================================
#
# Ported verbatim from the original quant-factor domain's ``checklists.py``
# (``CANONICAL_STAGE_ORDER`` + ``STAGE_CHECKLISTS``). These encode the integrity
# discipline of empirical factor research: state the economic mechanism before
# testing, fix the evaluation protocol and costs in advance, keep data
# point-in-time, log every trial, discount for multiple testing, and deliver an
# interpretable report whose every number traces back to a backtest row.

CHECKLIST_STAGE_ORDER: tuple[str, ...] = (
    "research",
    "plan",
    "benchmark",
    "run",
    "analysis",
    "draft",
    "review",
    "submission",
)


def _checklist(*items: ChecklistItem) -> tuple[ChecklistItem, ...]:
    return tuple(items)


CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": _checklist(
        ChecklistItem(
            id="research.hypotheses",
            statement=(
                "Each factor hypothesis states an economic / market-mechanism "
                "rationale for why it should predict future returns, and an "
                "expected sign — written BEFORE any backtest, grounded in "
                "literature or documented market structure."
            ),
            evidence_hint="research/FACTOR_HYPOTHESES.json",
        ),
        ChecklistItem(
            id="research.go_no_go",
            statement=(
                "An explicit GO/NO-GO decision is recorded for whether each "
                "hypothesis is worth testing, with its rationale and expected "
                "direction fixed in advance — so a later result cannot "
                "retro-justify a factor that had no prior thesis."
            ),
            evidence_hint="research/GO_NO_GO.json",
        ),
        ChecklistItem(
            id="research.prior_art",
            statement=(
                "Known/existing factors this hypothesis overlaps with are "
                "identified up front, so novelty and redundancy versus the "
                "standard factor zoo are understood before testing."
            ),
            evidence_hint="research/PRIOR_FACTORS.tsv",
        ),
    ),
    "plan": _checklist(
        ChecklistItem(
            id="plan.data_pit_provenance",
            statement=(
                "Data sources, timestamps, point-in-time / revision handling, "
                "corporate-action adjustment policy, and universe construction "
                "are disclosed; inputs are as-known-at-decision-time, not "
                "restated or back-filled."
            ),
            evidence_hint="plan/DATA_PROVENANCE.md",
        ),
        ChecklistItem(
            id="plan.eval_protocol",
            statement=(
                "The train/validation/test split (or walk-forward windows) is "
                "fixed in advance, the test set is quarantined, and the rule for "
                "when (and how many times) it may be touched is stated."
            ),
            evidence_hint="plan/EVAL_PROTOCOL.json",
        ),
        ChecklistItem(
            id="plan.cost_model_predeclared",
            statement=(
                "Transaction-cost, slippage, and turnover-cost assumptions are "
                "declared BEFORE screening, with source/justification, so costs "
                "cannot be tuned after seeing results."
            ),
            evidence_hint="plan/COST_MODEL.json",
        ),
        ChecklistItem(
            id="plan.metrics",
            statement=(
                "The evaluation metrics and acceptance thresholds (IC/RankIC, "
                "ICIR, long-short return, turnover, cost-adjusted return, etc.) "
                "are decided in advance, not chosen after seeing which ones look "
                "best."
            ),
            evidence_hint="plan/METRICS.json",
        ),
    ),
    "benchmark": _checklist(
        ChecklistItem(
            id="benchmark.no_lookahead",
            statement=(
                "The backtest harness is verified free of look-ahead: the factor "
                "at time t uses only information available at t, and future "
                "returns t -> t+h are aligned to the factor without leaking "
                "future data into the signal."
            ),
            evidence_hint="benchmark/LEAKAGE_CHECKS.md",
        ),
        ChecklistItem(
            id="benchmark.universe_bias_free",
            statement=(
                "The investable universe is survivorship- and selection-bias "
                "controlled: delisted / dead names are included as-of-date, and "
                "membership is reconstructed point-in-time."
            ),
            evidence_hint="benchmark/UNIVERSE_AUDIT.md",
        ),
        ChecklistItem(
            id="benchmark.baseline",
            statement=(
                "At least one baseline / benchmark (a known factor and/or the "
                "market) is wired in, so new factors are judged on incremental "
                "value rather than in a vacuum."
            ),
            evidence_hint="benchmark/BASELINE.json",
        ),
        ChecklistItem(
            id="benchmark.harness_authentic",
            statement=(
                "The backtest engine runs end-to-end on a known sanity case and "
                "reproduces an expected result, proving the substrate is "
                "trustworthy before any broad search begins."
            ),
            evidence_hint="benchmark/SANITY_RUN.md",
        ),
    ),
    "run": _checklist(
        ChecklistItem(
            id="run.search_ledger_complete",
            statement=(
                "Every backtest trial attempted (factor, combination, weighting, "
                "window, params) is appended to the search ledger at execution "
                "time — including failures and discards — so the full search "
                "breadth is auditable and cherry-picking is visible."
            ),
            evidence_hint="run/SEARCH_LEDGER.jsonl",
        ),
        ChecklistItem(
            id="run.screening",
            statement=(
                "Factor screening over the library applies the pre-declared "
                "metrics/thresholds; the screen criteria and the surviving "
                "factors are recorded."
            ),
            evidence_hint="run/SCREEN_RESULTS.tsv",
        ),
        ChecklistItem(
            id="run.combinations",
            statement=(
                "Combinations are built explicitly (equal-weight and/or "
                "optimized-weight), with the weighting method and any "
                "optimization inputs recorded per combination."
            ),
            evidence_hint="run/COMBINATIONS.json",
        ),
        ChecklistItem(
            id="run.cost_model_applied",
            statement=(
                "The pre-declared cost model is actually applied in every "
                "reported backtest: all headline returns are net of transaction "
                "costs and slippage."
            ),
            evidence_hint="run/SEARCH_LEDGER.jsonl",
        ),
    ),
    "analysis": _checklist(
        ChecklistItem(
            id="analysis.evidence",
            statement=(
                "Each surviving factor/combination is characterized with the "
                "full evidence set: IC/RankIC, ICIR, quantile monotonicity, "
                "long-short return, turnover, and cost-adjusted return."
            ),
            evidence_hint="analysis/FACTOR_EVIDENCE.json",
        ),
        ChecklistItem(
            id="analysis.test_set_quarantine",
            statement=(
                "Reported headline performance is out-of-sample under the fixed "
                "protocol: the test set was not iteratively tuned on, and any "
                "retest / peeking is disclosed in the ledger and the metric "
                "downgraded accordingly."
            ),
            evidence_hint="analysis/OOS_REPORT.md",
        ),
        ChecklistItem(
            id="analysis.multiple_testing",
            statement=(
                "Data-mining / multiple-testing risk is quantified from the "
                "search-ledger breadth (e.g. deflated metric, haircut, or FDR "
                "control); headline numbers are discounted for the number of "
                "trials run."
            ),
            evidence_hint="analysis/MULTIPLE_TESTING.md",
        ),
        ChecklistItem(
            id="analysis.independence",
            statement=(
                "Each selected factor's incremental value over known factors is "
                "shown (orthogonalization / correlation to the existing factor "
                "set), so the alpha is not a repackaged known factor."
            ),
            evidence_hint="analysis/ORTHOGONALITY.tsv",
        ),
        ChecklistItem(
            id="analysis.claims",
            statement=(
                "Every quantitative claim the report will make is bound to its "
                "raw backtest rows in the search ledger and to the figure/table "
                "that will show it."
            ),
            evidence_hint="analysis/CLAIM_GRAPH.json",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.report",
            statement=(
                "The factor report states, for each selected factor/combination, "
                "WHY it was chosen — the economic interpretation plus the "
                "supporting evidence — not merely its performance numbers."
            ),
            evidence_hint="report/FACTOR_REPORT.md",
        ),
        ChecklistItem(
            id="draft.limitations",
            statement=(
                "The report discloses limitations and risks: regime dependence, "
                "capacity / liquidity, alpha decay, crowding, and the search "
                "breadth behind the result."
            ),
            evidence_hint="report/FACTOR_REPORT.md",
        ),
        ChecklistItem(
            id="draft.figures",
            statement=(
                "Headline evidence (IC over time, quantile curves, long-short "
                "equity, OOS-vs-IS) is presented as figures/tables grounded in "
                "the analysis artifacts."
            ),
            evidence_hint="report/figures/",
        ),
    ),
    "review": _checklist(
        ChecklistItem(
            id="review.interpretability",
            statement=(
                "Every selected factor carries a coherent economic "
                "interpretation; none is kept purely because it backtests well "
                "with no plausible mechanism."
            ),
            evidence_hint="report/FACTOR_REPORT.md",
        ),
        ChecklistItem(
            id="review.evidence_grounded",
            statement=(
                "Every number in the report traces to a search-ledger row or an "
                "analysis artifact; no figure or claim is un-sourced or a "
                "placeholder."
            ),
            evidence_hint="report/FACTOR_REPORT.md",
        ),
        ChecklistItem(
            id="review.search_disclosed",
            statement=(
                "The report honestly discloses the search breadth (how many "
                "factors / combinations were tried) and the multiple-testing "
                "adjustment, so a referee can judge cherry-picking."
            ),
            evidence_hint="report/FACTOR_REPORT.md, run/SEARCH_LEDGER.jsonl",
        ),
    ),
    "submission": _checklist(
        ChecklistItem(
            id="submission.reproducible",
            statement=(
                "The report package is reproducible: data snapshot/version, "
                "code/config hash, random seeds, and the complete search ledger "
                "are included so an independent reviewer can re-run and audit "
                "the result."
            ),
            evidence_hint="report/REPRO_MANIFEST.json",
        ),
        ChecklistItem(
            id="submission.ledger_complete",
            statement=(
                "The disclosed search ledger is the complete one used: the "
                "trial counts match the search breadth the report claims."
            ),
            evidence_hint="report/REPRO_MANIFEST.json, run/SEARCH_LEDGER.jsonl",
        ),
        ChecklistItem(
            id="submission.assurance",
            statement=(
                "A final assurance statement certifies no look-ahead, "
                "point-in-time data, out-of-sample results, costs applied, and "
                "full search disclosed."
            ),
            evidence_hint="report/ASSURANCE.md",
        ),
    ),
}


# The integrity floor for factor mining: self-evolution may strengthen these but
# never weaken them. Ported from the original domain's PROTECTED_ITEM_IDS.
PROTECTED_ITEM_IDS: frozenset[str] = frozenset(
    {
        "research.go_no_go",
        "plan.data_pit_provenance",
        "plan.cost_model_predeclared",
        "benchmark.no_lookahead",
        "benchmark.universe_bias_free",
        "run.search_ledger_complete",
        "analysis.test_set_quarantine",
        "analysis.multiple_testing",
        "analysis.claims",
        "review.interpretability",
        "submission.reproducible",
    }
)


#: Quant missions complete on a certified final factor REPORT (report
#: certification, the research/EMNLP analog) — NOT a numeric metric.
completion_gate = "full_paper"


def role_banner(_role: str = "engineer") -> str:
    """Top-of-prompt framing for the quant-factor (finance) mission.

    Unlike the research vertical (which leaves the paper-authored prompts as-is),
    the quant vertical reframes the mission as factor mining and pins the
    empirical-integrity floor so planner/reviewer/engineer never drift into
    treating a high backtest number as the goal.
    """
    return (
        "MISSION — QUANT-FACTOR RESEARCH (A-share factor mining). The deliverable\n"
        "is an interpretable, reviewer-certified FACTOR REPORT arguing WHICH\n"
        "factors were selected and WHY (economic mechanism + evidence), NOT a pile\n"
        "of backtests and NOT a single numeric metric. A high backtest number\n"
        "never overrides an integrity failure. Non-negotiable integrity floor:\n"
        "state the economic mechanism and a GO/NO-GO BEFORE testing; fix the\n"
        "eval protocol and costs in advance; keep data point-in-time and the\n"
        "universe survivorship-bias-free with no look-ahead; log EVERY trial\n"
        "(including failures) to the search ledger via the BacktestExecutor;\n"
        "report out-of-sample numbers discounted for multiple testing; and ground\n"
        "every reported number in a ledger row.\n"
    )


__all__ = [
    "STAGE_ORDER",
    "STAGE_CHECKS",
    "REVIEWER_CHECKLISTS",
    "_PIPELINE_CHECK",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "PROTECTED_ITEM_IDS",
    "role_banner",
    "completion_gate",
]
