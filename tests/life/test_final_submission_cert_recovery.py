from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.manuscript_snapshot import manuscript_snapshot
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.stage_certificate import record_stage_review
from argus.core.venue_review import paper_review_snapshot
from argus.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
)
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._planning_cycle_helpers import (
    _PlanCycleState,
    _research_project_done_issue,
)
from argus.planner import PlannerVerdict
from argus.skills.vertical_select import persist_vertical
from argus.webapi.map_view import read_map


class _CompletingManager:
    def bind_execution_workdir(self, _workdir: Path) -> "_CompletingManager":
        return self

    def decide_stage_transition(self, **kwargs):  # noqa: ANN003
        assert kwargs["review"].review_source == "reviewer"
        return SimpleNamespace(
            action="complete",
            target_stage="review",
            current_stage="review",
            reason="The independent final review is done.",
            source="manager_llm",
            diagnostic="",
        )


class _Runner:
    def __init__(self) -> None:
        self.manager = _CompletingManager()


def _accepted_venue_review():
    return {
        "venue": "ICLR", "recommendation": "weak_accept", "acceptance_clear": True,
        "rationale": "The current paper's matched controls establish a useful, novel boundary.",
        "blocking_issues": [], "revision_required": False,
    }


def _make_final_review(
    tmp_path: Path,
    *,
    scope: str,
    bind_handoff: bool = False,
    separate_roots: bool = False,
) -> tuple[LifeSupervisor, Path, BacklogItem]:
    project = tmp_path / "project"
    life = tmp_path / "life"
    state_root = life if separate_roots else project
    paper = project / "paper"
    paper.mkdir(parents=True)
    (paper / "main.tex").write_text("final source\n", encoding="utf-8")
    (paper / "main.pdf").write_bytes(b"final pdf")
    (paper / "REVIEW.md").write_text(
        "Decision: done\nThe final submission satisfies the review gate.\n",
        encoding="utf-8",
    )
    persist_vertical(state_root, "research", research_target_level="exploratory", target_venue="ICLR")
    state_path = state_root / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "review"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    memory = LifeMemory.open(life)
    sink = JsonlEventSink(None, life_dir=life, verbosity="full")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            open_ended=True,
            final_certification_gate=True,
            project_worktree=project,
            artifact_root=state_root,
        ),
    )
    item = memory.backlog.add(
        BacklogItem.new(
            title="Final paper review",
            objective="Run the final submission review.",
            acceptance_check="Independent Reviewer returns done.",
            tags=[
                "planner",
                f"scope:{scope}",
                "review:required",
                "stage:review",
            ],
        )
    )
    mission_path = create_mission_context(
        life_dir=life,
        mission_id=item.id,
        stage="review",
        objective=item.objective,
        acceptance_check=item.acceptance_check,
        scope=scope,
        execution_workdir=str(project),
        tags=item.tags,
    )
    review = SimpleNamespace(
        status="done",
        reason="The exact final candidate satisfies the complete checklist.",
        next_action="",
        operator_question="",
        review_source="reviewer" if bind_handoff else "",
        manuscript_snapshot=manuscript_snapshot(project) if bind_handoff else None,
        venue_review=_accepted_venue_review(),
        venue_review_snapshot=paper_review_snapshot(project),
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="The final candidate is ready.",
        review=review,
        checkpoint_path=None,
    )
    record_stage_review(
        state_root=life,
        project_root=project,
        stage="review",
        item=item,
        manager_action="hold",
        manager_reason="The old runtime deferred final-stage consumption.",
        manuscript_binding=manuscript_snapshot(project),
        contract_root=state_root,
        venue_review=_accepted_venue_review(),
        venue_review_snapshot=paper_review_snapshot(project),
    )
    memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    item = next(row for row in memory.backlog.history() if row.id == item.id)
    supervisor._emit(
        {
            "type": "life.mission.completed",
            "item_id": item.id,
            "title": item.title,
            "scope": scope,
            "independent_review_required": True,
            "success": True,
            "status": "done",
            "final_submission_certified": False,
        }
    )
    return supervisor, project, item


def test_old_weak_accept_cannot_close_a_new_strong_accept_goal(tmp_path: Path) -> None:
    supervisor, _project, _item = _make_final_review(
        tmp_path, scope="final_submission", bind_handoff=True, separate_roots=True,
    )
    state_root = supervisor._artifact_root()
    state = read_pipeline_state(state_root)
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(state_root, state)

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) != "complete"
    assert not supervisor._journal_has_final_certification()


def test_current_signature_cannot_hide_a_rating_below_the_operator_bar(tmp_path: Path) -> None:
    supervisor, project, item = _make_final_review(
        tmp_path, scope="final_submission", bind_handoff=True, separate_roots=True,
    )
    state_root = supervisor._artifact_root()
    state = read_pipeline_state(state_root)
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(state_root, state)
    supervisor._emit({
        "type": "life.mission.completed", "item_id": item.id,
        "scope": "final_submission", "success": True, "status": "done",
        "final_submission_certified": True,
        "final_submission_signature": supervisor._final_submission_signature(),
        "manuscript_snapshot": manuscript_snapshot(project),
        "venue_review": _accepted_venue_review(),
        "venue_review_snapshot": paper_review_snapshot(project),
    })

    assert not supervisor._journal_has_final_certification()
    assert _research_project_done_issue(
        state_root, supervisor.memory.journal.all(), evidence_root=project,
    ) == "missing_exploratory_reviewer_certification"


@pytest.mark.parametrize("completion_path", ["planner", "bounded"])
@pytest.mark.parametrize("changed_path", ["paper/main.tex", "paper/main.pdf"])
def test_completion_uses_separate_execution_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completion_path: str,
    changed_path: str,
) -> None:
    supervisor, project, item = _make_final_review(
        tmp_path,
        scope="final_submission",
        bind_handoff=True,
        separate_roots=True,
    )
    # A final Reviewer/Manager settlement already exists. Only its consumption
    # is under test here; no recovery or LLM stage decision is needed.
    assert supervisor._emit({
        "type": "life.mission.completed",
        "item_id": item.id,
        "scope": "final_submission",
        "success": True,
        "status": "done",
        "final_submission_certified": True,
        "final_submission_signature": supervisor._final_submission_signature(),
        "manuscript_snapshot": manuscript_snapshot(project),
        "venue_review": _accepted_venue_review(),
        "venue_review_snapshot": paper_review_snapshot(project),
    })
    supervisor.memory.backlog.mark_done(item.id)
    assert supervisor._journal_has_final_certification() is True
    supervisor.config.final_certification_gate = False
    if completion_path == "bounded":
        from argus.skills import vertical_select

        supervisor.config.open_ended = False
        monkeypatch.setattr(
            vertical_select,
            "vertical_completion_certificate_status",
            lambda *_args: {"ok": True},
        )

    def certification_consumed() -> bool:
        if completion_path == "bounded":
            return bool(supervisor._bounded_completion_reason())
        state = _PlanCycleState(None)
        state.verdict = PlannerVerdict(
            project_done=True,
            new_tasks=[],
            summary="The current final submission is independently certified.",
        )
        supervisor._pc_normalize_project_done(state)
        return state.verdict.project_done

    assert certification_consumed() is True
    (project / changed_path).write_bytes(b"changed after certification\n")
    assert certification_consumed() is False


@pytest.mark.parametrize("changed_path", ["paper/main.tex", "paper/main.pdf"])
def test_existing_final_review_closes_gate(
    tmp_path: Path,
    changed_path: str,
) -> None:
    supervisor, project, _item = _make_final_review(
        tmp_path,
        scope="final_submission",
    )

    assert _research_project_done_issue(
        project, supervisor.memory.journal.all()
    ) == "missing_exploratory_reviewer_certification"
    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == "complete"
    assert _research_project_done_issue(
        project, supervisor.memory.journal.all()
    ) == ""
    assert supervisor._journal_has_final_certification() is True

    candidate = project / changed_path
    if candidate.suffix == ".pdf":
        candidate.write_bytes(b"changed pdf")
    else:
        candidate.write_text("changed source\n", encoding="utf-8")
    assert _research_project_done_issue(
        project, supervisor.memory.journal.all()
    ) == "missing_exploratory_reviewer_certification"
    assert supervisor._journal_has_final_certification() is False


def test_completing_on_a_deferred_review_certifies_that_task_on_the_map(
    tmp_path: Path,
) -> None:
    """s-757b9dac: the Planner's task passed review with its stage
    certification deferred, and the Manager then completed the objective on
    that review. Only the backlog row learned it: the task's completion still
    read deferred and not complete, so the map's banner said nothing was
    confirmed beside a card that said the review passed."""
    supervisor, _project, item = _make_final_review(tmp_path, scope="bounded")
    supervisor.config.open_ended = False
    supervisor.memory.backlog.update(item.id, started_ts=0.0)

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == "complete"

    dataset = read_map("s-cert", tmp_path, tmp_path / "life")
    (task,) = [task for task in dataset["tasks"] if task["id"] == item.id]
    assert task["status"] == "done"
    assert task["outcome"].get("review_status") == "done"
    assert task["outcome"].get("stage_certification") == "certified"
    completion = [
        event for event in dataset["events"]
        if event["item_id"] == item.id and event["type"] == "life.mission.completed"
    ][-1]
    assert completion["overall_complete"] is True
    assert completion["text"] == "The exact final candidate satisfies the complete checklist."
    assert not supervisor._journal_has_final_certification()


@pytest.mark.parametrize("separate_roots", [False, True])
def test_legacy_review_rejects_pdf_changed_before_recovery(
    tmp_path: Path,
    separate_roots: bool,
) -> None:
    supervisor, project, _item = _make_final_review(
        tmp_path,
        scope="final_submission",
        separate_roots=separate_roots,
    )
    (project / "paper" / "main.pdf").write_bytes(b"changed before recovery")

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == ""
    assert _research_project_done_issue(
        supervisor._artifact_root(),
        supervisor.memory.journal.all(),
        evidence_root=project,
    ) == "missing_exploratory_reviewer_certification"


@pytest.mark.parametrize("separate_roots", [False, True])
def test_legacy_review_without_pdf_timing_proof_fails_closed(
    tmp_path: Path,
    separate_roots: bool,
) -> None:
    supervisor, project, _item = _make_final_review(
        tmp_path,
        scope="final_submission",
        separate_roots=separate_roots,
    )
    certificate_path = tmp_path / "life" / "stage-certificates.json"
    certificates = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificates["stages"]["review"].pop("recorded_at")
    certificate_path.write_text(json.dumps(certificates), encoding="utf-8")

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == ""
    assert _research_project_done_issue(
        supervisor._artifact_root(),
        supervisor.memory.journal.all(),
        evidence_root=project,
    ) == "missing_exploratory_reviewer_certification"


@pytest.mark.parametrize("changed_path", ["paper/main.tex", "paper/main.pdf"])
@pytest.mark.parametrize("separate_roots", [False, True])
def test_changed_candidate_is_rejected_before_certification_recovery(
    tmp_path: Path,
    changed_path: str,
    separate_roots: bool,
) -> None:
    supervisor, project, _item = _make_final_review(
        tmp_path,
        scope="final_submission",
        bind_handoff=True,
        separate_roots=separate_roots,
    )
    candidate = project / changed_path
    if candidate.suffix == ".pdf":
        candidate.write_bytes(b"changed before recovery")
    else:
        candidate.write_text("changed before recovery\n", encoding="utf-8")

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == ""
    assert _research_project_done_issue(
        supervisor._artifact_root(),
        supervisor.memory.journal.all(),
        evidence_root=project,
    ) == "missing_exploratory_reviewer_certification"
