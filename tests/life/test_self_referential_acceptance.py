from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime_backends import _Outcome
from argus.core.models import RunnerResult
from argus.life.memory import BacklogItem, LifeMemory, park_reason
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical

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
    assert stored.status == "parked"
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "cyclic"

    reopened, replacement, _unused = _supervisor(tmp_path, None)
    # Drop the extra fixture task so restart contains only the paused original.
    reopened.memory.backlog.remove(_unused.id)
    reopened.tick()
    assert replacement.executed == []
    assert replacement.planner_backend.calls == []
    # Explicit resumption with the unchanged contract must reuse the same proof.
    reopened.memory.backlog.update(item.id, pending_question="", operator_decision={})
    reopened.memory.backlog.resume_parked()
    reopened.tick()
    assert replacement.executed == []
    assert replacement.planner_backend.calls == []
    assert next(row for row in reopened.memory.backlog.all() if row.id == item.id).status == "parked"
    assert len(runner.planner_backend.calls) == 1


@pytest.mark.parametrize("payload", [
    {"status": "assessed", "dependencies": [{**_dependency(), "source_quote": "invented condition"}]},
    {"status": "needs_clarification", "dependencies": [], "reason": "No report path is specified."},
    {"status": "confirmed", "cycle": ["REPORT.md", "receipt"]},
])
def test_invalid_interpretation_retries_without_asking_the_operator(
    tmp_path: Path, payload, monkeypatch,
):
    supervisor, runner, item = _supervisor(tmp_path, payload)

    result = supervisor.tick()

    assert runner.executed == []
    assert result["success"] is False
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert (stored.status, park_reason(stored)) == ("parked", "infra")
    assert stored.pending_question == ""
    assert "acceptance_dependency_assessment" not in stored.manager_decision
    assert stored.acceptance_check == CLAUSE
    assert len(runner.planner_backend.calls) == 2


def test_grounded_ambiguity_still_asks_a_readable_question(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "needs_clarification", "dependencies": [_dependency()],
        "reason": "Embedding may mean a link; this relation is tentative.",
    })

    supervisor.tick()

    assert runner.executed == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "parked"
    assert stored.pending_question == "Should the report include this review's result or refer to an earlier completed review?"
    assessment = stored.manager_decision["acceptance_dependency_assessment"]
    assert assessment["result"] == "unresolved"
    assert assessment["cycle"] == []
    assert stored.acceptance_check == CLAUSE
    supervisor.tick()
    assert len(runner.planner_backend.calls) == 1


@pytest.mark.parametrize("clause", [
    "Review the report and check current numerical results.",
    "Include the new figure in REPORT.md. The current Reviewer should assess scientific quality. Record changes in CHECKPOINT.md.",
    "The report must not include current Reviewer receipts; keep the review separate.",
    "Don't embed the current Reviewer receipt in the report.",
    "正文不得写入内部工作流、Reviewer 复核结论或当前交付状态。CHECKPOINT.md 记录修改。",
    "当前论文必须包含真实实验结果。Reviewer 按 ICLR 审稿，拒稿后 Engineer 在 Review 内补实验。CHECKPOINT.md 记录进展。",
])
def test_non_candidate_never_makes_a_dependency_model_call(tmp_path: Path, clause: str):
    supervisor, runner, _item = _supervisor(
        tmp_path, None, clause=clause,
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
    assert stored.status == "parked"
    assert "单独保存" in stored.pending_question
    assert "acceptance" not in stored.pending_question
    assert stored.manager_decision["acceptance_dependency_assessment"]["result"] == "cyclic"
    assert stored.acceptance_check == clause


def test_markdown_report_requirements_use_the_dependency_guard(tmp_path: Path):
    clause = "The report must contain:\n\n- current Reviewer done evidence;\n- zero pending questions/cards;\n- current closeout state."
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [{**_dependency(), "source_quote": clause}],
    }, clause=clause)

    supervisor.tick()

    assert runner.executed == []
    assert len(runner.planner_backend.calls) == 1
    assert next(row for row in supervisor.memory.backlog.all() if row.id == item.id).status == "parked"


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
    assert (stored.status, park_reason(stored)) == ("parked", "infra")
    assert stored.pending_question == ""
    assert "acceptance_dependency_assessment" not in stored.manager_decision


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


def test_invented_artifact_binding_is_an_internal_failure_even_with_a_real_source_quote(tmp_path: Path):
    supervisor, runner, item = _supervisor(tmp_path, {
        "status": "assessed", "dependencies": [{
            **_dependency(), "artifact": "invented.md", "subject": "invented.md",
        }],
    })

    supervisor.tick()

    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert runner.executed == []
    assert (stored.status, park_reason(stored)) == ("parked", "infra")
    assert stored.pending_question == ""
    assert "acceptance_dependency_assessment" not in stored.manager_decision


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
    ("budget_exhausted", "parked"),
    ("provider_cooldown", "parked"),
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
    assert result.get("park_reason", "") == ("" if status == "aborted" else kind)
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert "acceptance_dependency_assessment" not in stored.manager_decision
    assert stored.pending_question == ""
