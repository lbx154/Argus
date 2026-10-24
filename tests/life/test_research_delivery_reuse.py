"""A research request reviewed once, and unchanged since, is not reviewed again.

The research target gate wants a final review of the deliverable. When the
operator's own request was accepted by its independent Reviewer and every
delivered file still holds the delivered version, that review is the final
one; the Planner must not queue a second full review of the same files.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from argus.life.delivery import build_delivery_receipt
from argus.life.supervisor._planning_cycle_helpers import _research_project_done_issue
from argus.skills.vertical_select import persist_vertical

MISSING = "missing_exploratory_reviewer_certification"


def _reviewed_request(tmp_path: Path, **extra):
    state, workspace = tmp_path / "state", tmp_path / "work"
    state.mkdir()
    workspace.mkdir()
    persist_vertical(state, "research", research_target_level="exploratory")
    (workspace / "survey-zh.md").write_text("# 综述\n", encoding="utf-8")
    receipt = build_delivery_receipt(
        item_id="request-1", title="调研综述", summary="已通过独立复核。",
        success=True, overall_complete=False, status="done",
        review_status="done", review_source="reviewer",
        final_submission_certified=False, workspace=workspace, state_root=state,
        reviewer_artifacts=["survey-zh.md"], stage_package=True,
    )
    assert receipt is not None and receipt["kind"] == "stage_package"
    entry = SimpleNamespace(kind="mission_complete", ts=time.time() + 1, extra={
        "scope": "bounded", "success": True, "status": "done", "review_source": "reviewer",
        "operator_request": True, "execution_workdir": str(workspace), "delivery": receipt,
        **extra,
    })
    return state, workspace, entry


def test_an_unchanged_reviewed_request_satisfies_the_research_target(tmp_path) -> None:
    state, workspace, entry = _reviewed_request(tmp_path)

    assert _research_project_done_issue(state, [entry], evidence_root=workspace) == ""

    (workspace / "survey-zh.md").write_text("# 综述（改）\n", encoding="utf-8")
    assert _research_project_done_issue(state, [entry], evidence_root=workspace) == MISSING


def test_a_reviewed_plan_node_still_needs_the_final_review(tmp_path) -> None:
    state, workspace, entry = _reviewed_request(tmp_path, operator_request=False)

    assert _research_project_done_issue(state, [entry], evidence_root=workspace) == MISSING


def test_a_paper_keeps_its_venue_bound_certification(tmp_path) -> None:
    state, workspace, entry = _reviewed_request(tmp_path)
    (workspace / "paper").mkdir()
    (workspace / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")

    assert _research_project_done_issue(state, [entry], evidence_root=workspace) == MISSING
