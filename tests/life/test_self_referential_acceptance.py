from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_backends import _Outcome
from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.skills.vertical_select import persist_vertical

CLAUSE = "The final report must embed its current Reviewer done receipt and current closeout state."


def _dependency(*, placement="embedded", freshness="current_artifact", subject="REPORT.md"):
    return {
        "artifact": "REPORT.md", "receipt": "review-report", "subject": subject,
        "placement": placement, "freshness": freshness, "source_quote": CLAUSE,
    }


class _Backend:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[json.dumps(self.payload)])


class _Runner:
    def __init__(self, payload):
        self.planner_backend = _Backend(payload)
        self.executed = []
        self.usage_scopes = []

    @contextmanager
    def task_usage_context(self, mission_id):
        self.usage_scopes.append(mission_id)
        yield

    def execute(self, **kwargs):
        self.executed.append(kwargs)
        return _Outcome(
            success=True, status="done", final_review_status="done",
            final_review_source="reviewer", final_review_reason="Synthetic independent review passed.",
        )


def _supervisor(tmp_path: Path, payload, *, clause=CLAUSE):
    workdir = tmp_path / "project"
    workdir.mkdir(exist_ok=True)
    persist_vertical(workdir, "software")
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner(payload)
    supervisor = LifeSupervisor(
        memory=memory, runner=runner,
        sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(project_worktree=workdir, artifact_root=workdir),
    )
    item = memory.backlog.add(BacklogItem.new(
        title="Close the verification report", objective="Finish REPORT.md. " + clause,
        acceptance_check=clause, tags=["scope:bounded", "planner", "review:required"],
        non_goals=["Preserve all original substantive checks"], iterate=False,
        manager_decision={"routed": True, "vertical": "software"},
    ))
    return supervisor, runner, item


def test_self_referential_receipt_cannot_reach_a_stable_reviewed_artifact(tmp_path: Path):
    report = tmp_path / "REPORT.md"
    report.write_text("# Completed work\nAll substantive checks pass.\n")
    generations = []
    for generation in range(3):
        reviewed = hashlib.sha256(report.read_bytes()).hexdigest()
        receipt = {"generation": generation, "status": "done", "subject_sha256": reviewed}
        report.write_text("# Completed work\nAll substantive checks pass.\n" + json.dumps(receipt))
        current = hashlib.sha256(report.read_bytes()).hexdigest()
        assert current != receipt["subject_sha256"]
        generations.append(current)
    assert len(set(generations)) == 3


def test_supervisor_blocks_proven_self_receipt_cycle_without_executing(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [_dependency()],
    })

    result = supervisor.tick()

    assert runner.executed == []
    assert result["success"] is False
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.pending_question
    assert stored.acceptance_check == CLAUSE
    assert len(runner.planner_backend.calls) == 1
    assert runner.planner_backend.calls[0]["options"].disable_tools is True
    assert runner.usage_scopes == [f"{item.id}:attempt:1"]


@pytest.mark.parametrize("relation,clause", [
    (_dependency(placement="external"),
     "The final report must include a link; evaluate the current Reviewer done receipt externally."),
    (_dependency(freshness="historical"),
     "The final report must include a historical Reviewer receipt, not its current Reviewer done receipt."),
    (_dependency(subject="OTHER.md"),
     "The final report must embed the current Reviewer receipt for OTHER.md."),
])
def test_legitimate_receipt_dependencies_execute_and_keep_independent_review(
    tmp_path: Path, relation, clause: str,
):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [{**relation, "source_quote": clause}],
        "result": "cyclic", "cycle": ["untrusted model conclusion"],
    }, clause=clause)

    result = supervisor.tick()

    assert result["success"] is True
    assert len(runner.executed) == 1
    assert runner.executed[0]["require_independent_review"] is True
    assert runner.executed[0]["review_objective"].startswith(item.objective)
    assert len(runner.planner_backend.calls) == 1


def test_cached_cycle_survives_restart_without_another_assessment(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [_dependency()],
    })
    supervisor.tick()
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "cyclic"

    reopened, replacement, _unused = _supervisor(tmp_path, None)
    # Drop the extra fixture task so restart contains only the paused original.
    reopened.memory.backlog.remove(_unused.id)
    reopened.tick()
    assert replacement.executed == []
    assert replacement.planner_backend.calls == []
    # Explicit resumption with the unchanged contract must reuse the same proof.
    reopened.memory.backlog.update(item.id, pending_question="", operator_decision={})
    reopened.memory.backlog.resume_paused(item.id)
    reopened.tick()
    assert replacement.executed == []
    assert replacement.planner_backend.calls == []
    assert next(row for row in reopened.memory.backlog.all() if row.id == item.id).status == "paused_operator"
    assert len(runner.planner_backend.calls) == 1


@pytest.mark.parametrize("payload", [
    {"status": "assessed", "dependencies": [{**_dependency(), "source_quote": "invented condition"}]},
    {"status": "needs_clarification", "dependencies": [], "reason": "No report path is specified."},
    {"status": "needs_clarification", "dependencies": [_dependency()], "reason": "Embedding may mean a link; this relation is tentative."},
    {"status": "confirmed", "cycle": ["REPORT.md", "receipt"]},
])
def test_unknown_or_unquoted_relation_pauses_for_clarification_not_failure(
    tmp_path: Path, payload, monkeypatch,
):
    monkeypatch.setenv("ARGUS_SKILL_AUTONOMY_MODE", "autonomous")
    supervisor, runner, item = _supervisor(tmp_path, payload)

    result = supervisor.tick()

    assert runner.executed == []
    assert result["success"] is False
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assessment = stored.manager_decision["acceptance_dependency_assessment"]
    assert assessment["result"] == "unresolved"
    assert assessment["cycle"] == []
    assert stored.acceptance_check == CLAUSE
    supervisor.tick()
    assert len(runner.planner_backend.calls) == 1


def test_contract_revision_reassesses_but_real_content_edits_do_not_invalidate_structure(
    tmp_path: Path,
):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [_dependency()],
    })
    supervisor.tick()
    revised = "The final report must include a link; evaluate the current Reviewer done receipt externally."
    runner.planner_backend.payload = {
        "status": "assessed", "dependencies": [{
            **_dependency(placement="external"), "source_quote": revised,
        }],
    }
    supervisor.memory.backlog.update(
        item.id, pending_question="", operator_decision={},
        objective="Finish REPORT.md. " + revised, acceptance_check=revised,
    )
    supervisor.memory.backlog.resume_paused(item.id)
    assert supervisor.tick()["success"] is True
    assert len(runner.planner_backend.calls) == 2
    # A new report revision under the same external receipt contract is ordinary
    # work, so it executes/reviews again without repeating dependency analysis.
    (tmp_path / "project" / "REPORT.md").write_text("Substantive corrected content.")
    previous = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    followup = BacklogItem.new(
        title=previous.title, objective=previous.objective,
        original_objective=previous.original_objective,
        acceptance_check=previous.acceptance_check, non_goals=previous.non_goals,
        manager_decision=previous.manager_decision, tags=previous.tags, iterate=False,
    )
    supervisor.memory.backlog.add(followup)
    assert supervisor.tick()["success"] is True
    assert len(runner.planner_backend.calls) == 2
    assert len(runner.executed) == 2


def test_non_candidate_never_makes_a_dependency_model_call(tmp_path: Path):
    supervisor, runner, _item = _supervisor(
        tmp_path, None, clause="Review the report and check current numerical results.",
    )
    assert supervisor.tick()["success"] is True
    assert runner.planner_backend.calls == []


def test_chinese_legacy_task_and_named_planner_reply_use_the_production_guard(tmp_path: Path):
    clause = "最终报告必须包含本次 Reviewer 复核后生成的最新验收回执。"
    supervisor, runner, item = _supervisor(tmp_path, None, clause=clause)
    runner.planner_backend.run_exec = lambda **_kwargs: RunnerResult(
        exit_code=0, agent_messages=[
            "该要求把验收回执放回其被复核的报告中。\n"
            "DEPENDENCY_STATUS=assessed\nDEPENDENCY_REASON=本次回执依赖报告的当前版本。\n"
            "RECEIPT_ARTIFACT=REPORT.md\nRECEIPT_ID=review-report\n"
            "RECEIPT_SUBJECT=REPORT.md\nRECEIPT_PLACEMENT=embedded\n"
            "RECEIPT_FRESHNESS=current_artifact\nRECEIPT_QUOTE=" + clause,
        ],
    )

    supervisor.tick()

    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "cyclic"
    assert stored.acceptance_check == clause


def test_cancellation_provider_and_usage_scope_are_carried_to_dependency_call(tmp_path: Path, monkeypatch):
    supervisor, runner, _item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [_dependency(placement="external")],
    })
    cancel = lambda: "operator pause requested"
    monkeypatch.setattr(supervisor, "_planner_config", lambda: SimpleNamespace(
        model="synthetic-model", reasoning_effort="high",
        external_interrupt_reason_provider=cancel,
    ))

    supervisor.tick()

    options = runner.planner_backend.calls[0]["options"]
    assert options.external_interrupt_reason_provider is cancel
    assert options.model == "synthetic-model"
    assert options.force_safe_mode is True
    assert options.sandbox_mode == "read-only"
    assert runner.usage_scopes


@pytest.mark.parametrize("suffix", ["", "\nRECEIPT_ARTIFACT=REPORT.md\nRECEIPT_ID=external\n"
                         "RECEIPT_SUBJECT=OTHER.md\nRECEIPT_PLACEMENT=external\n"
                         "RECEIPT_FRESHNESS=current_artifact\nRECEIPT_QUOTE=" + CLAUSE])
def test_orphan_receipt_fields_cannot_be_discarded_as_an_empty_acyclic_assessment(
    tmp_path: Path, suffix: str,
):
    supervisor, runner, item = _supervisor(tmp_path, None)
    runner.planner_backend.run_exec = lambda **_kwargs: RunnerResult(
        exit_code=0, agent_messages=[
            "DEPENDENCY_STATUS=assessed\nDEPENDENCY_REASON=The current receipt depends on the same report.\n"
            "RECEIPT_ID=review-report\nRECEIPT_SUBJECT=REPORT.md\n"
            "RECEIPT_PLACEMENT=embedded\nRECEIPT_FRESHNESS=current_artifact\n"
            "RECEIPT_QUOTE=not present in the original task" + suffix,
        ],
    )

    supervisor.tick()

    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "unresolved"


def test_markdown_report_requirements_use_the_dependency_guard(tmp_path: Path):
    clause = "The report must contain:\n\n- current Reviewer done evidence;\n- zero pending questions/cards;\n- current closeout state."
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [{**_dependency(), "source_quote": clause}],
    }, clause=clause)

    supervisor.tick()

    assert runner.executed == []
    assert len(runner.planner_backend.calls) == 1
    assert next(row for row in supervisor.memory.backlog.all() if row.id == item.id).status == "paused_operator"


def test_receipt_field_overwrite_cannot_erase_a_preceding_cycle(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, None)
    first = "\n".join((
        "DEPENDENCY_STATUS=assessed", "DEPENDENCY_REASON=Explicit requirements.",
        "RECEIPT_ARTIFACT=REPORT.md", "RECEIPT_ID=report-review", "RECEIPT_SUBJECT=REPORT.md",
        "RECEIPT_PLACEMENT=embedded", "RECEIPT_FRESHNESS=current_artifact", "RECEIPT_QUOTE=" + CLAUSE,
    ))
    overwrite = "\n".join((
        "RECEIPT_ID=other", "RECEIPT_SUBJECT=OTHER.md", "RECEIPT_PLACEMENT=external",
        "RECEIPT_FRESHNESS=current_artifact", "RECEIPT_QUOTE=" + CLAUSE,
    ))
    runner.planner_backend.run_exec = lambda **_kwargs: RunnerResult(
        exit_code=0, agent_messages=[first + "\n" + overwrite],
    )

    supervisor.tick()

    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "unresolved"


def test_contract_changed_during_assessment_never_receives_the_stale_result(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, None)

    def change_contract(**_kwargs):
        supervisor.memory.backlog.update(item.id, acceptance_check="New operator acceptance.",
                                         manager_decision={**item.manager_decision, "fresh_instruction": True})
        return RunnerResult(exit_code=0, agent_messages=[json.dumps({
            "status": "assessed", "dependencies": [_dependency()],
        })])

    runner.planner_backend.run_exec = change_contract
    result = supervisor.tick()

    assert runner.executed == []
    assert result["status"] == "claim_lost"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.acceptance_check == "New operator acceptance."
    assert stored.manager_decision["fresh_instruction"] is True
    assert "acceptance_dependency_assessment" not in stored.manager_decision
    assert stored.pending_question == ""
    assert stored.status == "pending"


def test_assessment_merges_concurrent_manager_metadata_without_overwriting_it(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, None)

    def update_metadata(**_kwargs):
        supervisor.memory.backlog.update(item.id, manager_decision={
            **item.manager_decision, "fresh_instruction": "Keep this Manager record.",
        })
        return RunnerResult(exit_code=0, agent_messages=[json.dumps({
            "status": "assessed", "dependencies": [_dependency(placement="external")],
        })])

    runner.planner_backend.run_exec = update_metadata
    assert supervisor.tick()["success"] is True
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.manager_decision["fresh_instruction"] == "Keep this Manager record."
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "well_founded"


def test_invented_artifact_binding_is_unresolved_even_with_a_real_source_quote(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [{
            **_dependency(), "artifact": "invented.md", "subject": "invented.md",
        }],
    })

    supervisor.tick()

    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert runner.executed == []
    assert stored.status == "paused_operator"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "unresolved"


def test_superseded_external_contract_is_automatically_reassessed_before_execution(tmp_path: Path):
    external = "The final report must include a link; evaluate the current Reviewer done receipt externally."
    supervisor, runner, item = _supervisor(tmp_path, None, clause=external)
    calls = 0

    def revise(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            supervisor.memory.backlog.update(
                item.id, objective="Finish REPORT.md. " + CLAUSE, acceptance_check=CLAUSE,
            )
            relation = {**_dependency(placement="external"), "source_quote": external}
        else:
            relation = _dependency()
        return RunnerResult(exit_code=0, agent_messages=[json.dumps({
            "status": "assessed", "dependencies": [relation],
        })])

    runner.planner_backend.run_exec = revise
    assert supervisor.tick()["status"] == "claim_lost"
    assert supervisor.tick()["success"] is False
    assert calls == 2
    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "cyclic"


def test_task_aborted_during_assessment_is_not_resurrected_or_settled_again(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, None)

    def abort(**_kwargs):
        supervisor.memory.backlog.update(item.id, status="aborted")
        return RunnerResult(exit_code=0, agent_messages=[json.dumps({
            "status": "assessed", "dependencies": [_dependency()],
        })])

    runner.planner_backend.run_exec = abort
    assert supervisor.tick()["status"] == "claim_lost"
    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "aborted"
    assert stored.pending_question == ""


def test_discarded_example_before_final_footer_cannot_create_a_contract_hold(tmp_path: Path):
    supervisor, runner, _item = _supervisor(tmp_path, None)
    runner.planner_backend.run_exec = lambda **_kwargs: RunnerResult(
        exit_code=0, agent_messages=[
            "Rejected draft:\nRECEIPT_ID=missing-start\n\nDecision:\n"
            "DEPENDENCY_STATUS=assessed\nDEPENDENCY_REASON=External receipt only.\n"
            "RECEIPT_ARTIFACT=REPORT.md\nRECEIPT_ID=external\n"
            "RECEIPT_SUBJECT=REPORT.md\nRECEIPT_PLACEMENT=external\n"
            "RECEIPT_FRESHNESS=current_artifact\nRECEIPT_QUOTE=" + CLAUSE,
        ],
    )
    assert supervisor.tick()["success"] is True
    assert len(runner.executed) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_provider_failure_after_concurrent_abort_preserves_the_new_terminal_state(
    tmp_path: Path, raises: bool,
):
    supervisor, runner, item = _supervisor(tmp_path, None)

    def failed_after_abort(**_kwargs):
        supervisor.memory.backlog.update(item.id, status="aborted")
        if raises:
            raise RuntimeError("Synthetic provider failure")
        return RunnerResult(exit_code=1, stop_kind="budget_exhausted", fatal_error="Synthetic budget stop")

    runner.planner_backend.run_exec = failed_after_abort
    assert supervisor.tick()["status"] == "claim_lost"
    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "aborted"
    assert stored.pending_question == ""
    assert "acceptance_dependency_assessment" not in stored.manager_decision


@pytest.mark.parametrize("kind,status", [
    ("budget_exhausted", "paused_budget"),
    ("provider_cooldown", "paused_provider_cooldown"),
    ("operator_abort", "aborted"),
])
def test_provider_stop_remains_provider_stop_and_is_not_cached_as_a_contract_finding(
    tmp_path: Path, kind: str, status: str,
):
    supervisor, runner, item = _supervisor(tmp_path, None)
    runner.planner_backend.run_exec = lambda **_kwargs: RunnerResult(
        exit_code=1, fatal_error="Synthetic provider interruption", stop_kind=kind,
    )

    result = supervisor.tick()

    assert runner.executed == []
    assert result["status"] == status
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert "acceptance_dependency_assessment" not in stored.manager_decision
    assert stored.pending_question == ""
