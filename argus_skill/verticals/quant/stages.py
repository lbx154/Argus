"""Quant-factor research vertical — stage definitions and checklists.

The finance analog of ``argus_skill.verticals.research.stages``. It reuses the
SAME 8 stage ids as the paper pipeline
(``research → plan → benchmark → run → analysis → draft → review →
submission``) so every domain-agnostic mechanism that keys off stage ids keeps
working unchanged; the *finance* semantics are carried by the markdown
``CHECKLIST_ITEMS`` (ported from the original quant-factor domain's
``checklists.py``) and the ``role_banner``.

The deliverable is an interpretable, reviewer-certified **factor report**, so
``completion_gate`` is ``"full_paper"`` (report certification), exactly like
``research`` — NOT a numeric speedrun metric.

Two built-in finance skills back the Reviewer/Engineer prompts:

* ``reviewer/quant-factor-report-review.md`` — the strict quant-research referee
  rubric (economic interpretability, search breadth & multiple-testing, OOS
  discipline, no look-ahead / point-in-time data, costs, incremental value,
  evidence grounding, reproducibility); used on the report-shaped stages.
* ``engineer/quant-factor-loop.md`` — the 3-intent select/evaluate/decide loop
  and the non-negotiable ``BacktestExecutor`` / search-ledger contract; used on
  the run / analysis search stages.

Both skill files live under ``argus_skill/builtin_skills/{reviewer,engineer}/``.
"""
from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]


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
            id="research.hypothesis_priors",
            statement=(
                "The rationale, expected direction, and rejection conditions for "
                "each hypothesis are fixed before testing, so a later result cannot "
                "retro-justify a factor that had no prior thesis."
            ),
            evidence_hint="research/HYPOTHESIS_PRIORS.json",
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
            evidence_hint=(
                "run/SEARCH_LEDGER.jsonl — audit its tamper-evidence with "
                "`python -m argus_skill.verticals.quant.search_ledger verify "
                "--path run/SEARCH_LEDGER.jsonl`; a hand-written or edited "
                "ledger fails the chain. The chain says whether the rows are "
                "authentic, not whether the search was broad enough."
            ),
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
        "state the economic mechanism and rejection conditions BEFORE testing; fix the\n"
        "eval protocol and costs in advance; keep data point-in-time and the\n"
        "universe survivorship-bias-free with no look-ahead; log EVERY trial\n"
        "(including failures) to the search ledger via the BacktestExecutor;\n"
        "report out-of-sample numbers discounted for multiple testing; and ground\n"
        "every reported number in a ledger row.\n"
    )


__all__ = [
    "STAGE_ORDER",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "PROTECTED_ITEM_IDS",
    "role_banner",
    "completion_gate",
]
