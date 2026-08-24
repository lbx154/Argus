"""Lean production kernel-engineering vertical."""

from __future__ import annotations

import json

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
    except (OSError, ValueError, json.JSONDecodeError):
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
        "MISSION — improve the real kernel or inference path directly. Read only the "
        "repository instructions and existing evidence needed for the next decision; "
        "reuse the project toolchain and current primary sources when relevant. Establish "
        "a baseline, make one coherent implementation or configuration change, run the "
        "decisive correctness check and comparable target-hardware measurement, then "
        "retain or revert. Do not create process documents, stage bundles, proof packages, "
        "frontier ledgers, environment reports, or checkpoint churn unless the operator "
        "explicitly requests that artifact or a concise durable result is necessary for "
        "a later task."
    )
    if role == "planner":
        return (
            common
            + " Delegate substantive implementation and its verification in one task. "
            "Do not split audit, planning, implementation, validation, and reporting "
            "into separate ceremony nodes when one Engineer can perform them coherently. "
            "When a task launches or waits on a long external benchmark, fill spare "
            "mission slots with useful independent source analysis or implementation "
            "that does not need its result. Mark each companion `parallel_safe=true` "
            "with `owns_paths` disjoint from running work; never queue status polling "
            "or make-work."
        )
    if role == "reviewer":
        return (
            common
            + " Review the code, correctness oracle, benchmark comparability, and user "
            "impact; never fail work merely because a framework-specific document is absent."
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
