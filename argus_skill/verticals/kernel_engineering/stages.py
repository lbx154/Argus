"""Lean production kernel-engineering vertical."""

from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["optimize"]
STAGE_ALIASES = {
    stage: "optimize"
    for stage in (
        "scope",
        "discover",
        "environment",
        "baseline",
        "profiling",
        "optimization",
        "validate",
        "report",
        "deliver",
    )
}
WORKFLOW_MODE = "direct"
completion_gate = "none"
MISSION_KIND = "optimize"
VERIFICATION_STAGE_PROFILES = {"optimize": "develop"}
ENGINEER_LIVE_SEARCH_WORK_KINDS = {
    "algorithm_discovery": frozenset({"optimize"}),
    "engineering_optimization": frozenset({"optimize"}),
}

# Kernel work should start from the repository and measured behavior, not from
# framework-authored document bundles.
STAGE_PRIMARY_DELIVERABLES: dict[str, tuple[str, ...]] = {}
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {"optimize": []}
CHECKLIST_STAGE_ORDER: tuple[str, ...] = tuple(STAGE_ORDER)
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = tuple(STAGE_ORDER)

_ENGINEER_SKILL = "engineer/kernel-environment-first-engineering.md"
_REVIEWER_SKILL = "reviewer/kernel-engineering-review.md"

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "optimize": (
        ChecklistItem(
            id="optimize.measured_change",
            statement=(
                "Work starts from a reproducible baseline or current failing behavior, "
                "changes one coherent mechanism, preserves correctness, and uses the "
                "repository's real tests or benchmark to decide whether to retain it."
            ),
            evidence_hint=(
                "Relevant source diff plus command output from the real correctness "
                "check and paired benchmark; no dedicated report file is required."
            ),
        ),
    ),
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "optimize": (
        _REVIEWER_SKILL,
        "Judge the actual implementation and decisive evidence. Require correctness "
        "before performance claims, comparable warm measurements on the target "
        "hardware, and explicit regressions or fallback behavior. Do not require "
        "scope, frontier, environment-audit, baseline-protocol, outcome-taxonomy, "
        "validation-matrix, or results-report files when the source diff and command "
        "outputs already establish the result.",
        [],
    ),
}


def search_altitude_context(project_root) -> str:  # noqa: ARG001
    return ""


def planner_task_issues(stage: str, project_root, task) -> tuple[str, ...]:  # noqa: ARG001
    return ()


def stage_completion_issues(stage: str, project_root) -> tuple[str, ...]:  # noqa: ARG001
    return ()


def prepare_mission(  # noqa: ARG001 - baseline isolation is per stage, not per item
    *,
    stage: str,
    project_root,
    state_root,
    mission,
) -> str:
    """Preserve legacy explicit baseline isolation without making it a stage gate.

    Keyword-only because the framework forwards this hook by keyword; the
    parameter names are the contract. ``mission`` is accepted and unread: the
    baseline workspace is one shared tree per stage, and making it depend on
    which item claimed it would hand two concurrent missions two baselines.
    """
    raw_stage = str(stage or "").strip().lower()
    from ...core.pipeline_state import read_pipeline_state

    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        raw_stage = str(payload.get("current_stage") or raw_stage).strip().lower()
    if raw_stage != "baseline":
        return ""
    from .baseline_workspace import prepare_baseline_workspace

    try:
        baseline = prepare_baseline_workspace(project_root, state_root)
    except Exception as exc:
        return f"## Baseline isolation unavailable\n- error: {exc}"
    return baseline.prompt_block() if baseline is not None else ""


def role_banner(role: str) -> str:
    common = (
        "MISSION — maximize the real kernel or inference path while preserving room for "
        "curiosity. Explore high-upside mechanisms broadly, including radical, uncertain, "
        "cross-stack, and mutually competing ideas. Read repository evidence and current "
        "primary sources as deeply as useful. Do not prefer the smallest patch, the "
        "easiest immediate validation, or an idea that is already reproducible merely "
        "because it is safer to execute. Research reports and explicit hypotheses are "
        "valid exploration even before implementation. Distinguish hypotheses from "
        "claimed results. During exploration, one clean screen, an inconclusive attempt, "
        "or a report with no run may be sufficient; do not demand repeated trials, "
        "multiple seeds, confidence intervals, or baseline recertification. Reserve "
        "those costs for a genuinely promising candidate whose performance will be "
        "claimed or retained. Only such a result requires the decisive correctness "
        "check and comparable target-hardware measurement. Do not "
        "create process documents, stage bundles, proof packages, frontier ledgers, "
        "environment reports, or checkpoint churn unless the operator explicitly "
        "requests that artifact or a concise durable result is necessary for later work."
    )
    if role == "planner":
        return (
            common
            + " Delegate substantive implementation and its verification in one task. "
            "Do not split audit, planning, implementation, validation, and reporting "
            "into separate ceremony nodes when one Engineer can perform them coherently. "
            "Proactively use fresh primary-source research whenever external systems, "
            "papers, issues, or kernels could materially improve the plan; do not wait "
            "for repeated failures or a constraint change. A bounded report-only "
            "`work_kind=algorithm_discovery` task is valid when its synthesis can guide "
            "later engineering. It may run path-disjoint during an external benchmark, "
            "and it does not need to produce code or an executable gate. Maintain a "
            "portfolio of genuinely different mechanisms instead of converging early on "
            "the nearest implementation. Parallel research tasks may investigate "
            "independent mechanism families when mission slots permit. Prefer expected "
            "upside and information gain over low execution risk. Do not spend mission "
            "slots repeating seeds, controls, or unchanged benchmarks for exploratory "
            "ideas; one clean screen is enough until a candidate is promising. "
            "When at least two mission slots are available, keep one conversion lane "
            "active whenever a screened or implemented candidate still lacks an "
            "end-to-end target measurement. That lane owns runtime integration through "
            "one decisive screen or a concrete terminal failure; other slots remain "
            "free for unconstrained exploration. Do not let another report or mechanism "
            "variant displace ready conversion work. "
            "When a long external benchmark is already running or a task will launch "
            "one, use the same decision to fill spare mission slots with useful "
            "independent source analysis or implementation that does not need its "
            "result. Mark each companion `parallel_safe=true` with `owns_paths` "
            "disjoint from running work; never queue status polling or make-work."
        )
    if role == "engineer":
        return (
            common
            + " Treat unattended benchmark and profiler runs as asynchronous work: "
            "leave durable status, then use the wait window for independent hot-path "
            "reading or implementation within the mission's owned paths. Do not "
            "foreground-poll or spend a round only checking status. On an "
            "`algorithm_discovery` mission, inspect current primary sources rather than "
            "generic summaries, follow surprising leads, compare genuinely different "
            "mechanism families, and produce a concise decision-useful research report. "
            "Implementation, immediate verification, and immediate reproducibility are "
            "optional unless the task explicitly asks for them. Prefer high-upside, "
            "high-uncertainty mechanisms over low-risk incrementalism when their potential "
            "justifies exploration. Use one clean run for exploratory screening; do not "
            "default to multi-seed or repeated-run campaigns. Once implementation begins, "
            "keep it one coherent implementation rather than unrelated edits. On a "
            "conversion-lane task, continue through real runtime wiring and the "
            "end-to-end screen instead of stopping at a microbenchmark or source-complete "
            "artifact."
        )
    if role == "reviewer":
        return (
            common
            + " Review the code, correctness oracle, benchmark comparability, and user "
            "impact; never fail work merely because a framework-specific document is absent. "
            "A report-only research mission is valid: review source quality, factual "
            "accuracy, synthesis, and usefulness to the next decision without demanding "
            "an implementation or executable gate. Do not reject a speculative, radical, "
            "or not-yet-reproducible idea merely for lacking immediate validation; require "
            "evidence only when the report presents a hypothesis as an established result. "
            "Never demand multiple seeds or repeated trials for an exploratory screen; "
            "request them only for a promising candidate being claimed or retained."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_OPTIONAL_STAGES",
    "CHECKLIST_STAGE_ORDER",
    "ENGINEER_LIVE_SEARCH_WORK_KINDS",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ALIASES",
    "STAGE_ORDER",
    "STAGE_PRIMARY_DELIVERABLES",
    "WORKFLOW_MODE",
    "completion_gate",
    "planner_task_issues",
    "prepare_mission",
    "role_banner",
    "search_altitude_context",
    "stage_completion_issues",
]
