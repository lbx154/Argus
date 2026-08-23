"""Reviewer cadence follows the explicit independent-review contract."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    parse_continue_work_request,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "reviewed",
        "next_action": "",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_legacy_continue_work_parser_remains_compatible() -> None:
    assert parse_continue_work_request(
        "Changed the parser.\nCONTINUE_WORK: wire it into the runner"
    ) == "wire it into the runner"
    assert parse_continue_work_request("CONTINUE_WORK: wire it in") is None


def test_continue_work_text_does_not_skip_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "## Verification (verbatim)\n```\n1 passed\n```\n"
                "CONTINUE_WORK: wire it into the runner"
            ),
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    events: list[dict] = []
    status, rounds, _final, _reason, tid = _engineer(backend).run(
        objective="always review",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:2] == ["engineer-r1", "reviewer"]
    assert not any(event["type"] == "round.review.deferred" for event in events)
    assert status == "done"
    assert len(rounds) == 1
    assert tid is None
    assert [
        resume for label, resume in backend.resume_history
        if label in {"engineer-r1", "reviewer"}
    ] == [None, None]


def test_low_risk_task_can_finish_with_engineer_self_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Implemented the bounded fix.\n## Verification\n3 tests passed\n"
                "`MILESTONE_STATUS=done`"
            ),
            thread_id="t1",
        ),
    )

    events: list[dict] = []
    status, rounds, _final, reason, tid = _engineer(backend).run(
        objective="low-risk repair with decisive tests",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            require_independent_review=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "done"
    assert len(rounds) == 1
    assert rounds[0].review.review_source == "engineer_self_review"
    review_events = [event for event in events if event["type"] == "round.review.completed"]
    assert review_events[0]["review_source"] == "engineer_self_review"
    assert "independent review was not required" in reason
    assert "Host-defined" not in reason
    assert tid is None


def test_engineer_continues_milestone_without_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Captured the signal.\nMILESTONE_STATUS=continue",
            thread_id="t1",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Made the keep/reject decision.\nMILESTONE_STATUS=done",
            thread_id="t2",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete one decision-sized milestone",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            require_independent_review=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
    ]
    assert len(rounds) == 1


def test_engineer_operator_question_parks_without_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "The required choice belongs to the operator.\n"
                "MILESTONE_STATUS=continue\n"
                "`OPERATOR_QUESTION=请选择 A 或 B`\n"
                "`OPERATOR_OPTIONS=route-a :: 选择 A :: 使用 A 路线继续。; "
                "route-b :: 选择 B :: 使用 B 路线继续。`"
            ),
            thread_id="t1",
        ),
    )

    events: list[dict] = []
    status, rounds, _final, reason, _tid = _engineer(backend).run(
        objective="write the operator-selected value",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            require_independent_review=True,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert len(rounds) == 1
    assert rounds[0].review.review_source == "engineer_operator_question"
    assert rounds[0].review.operator_question == "请选择 A 或 B"
    assert [option["label"] for option in rounds[0].review.operator_options] == [
        "选择 A",
        "选择 B",
    ]
    assert rounds[0].review.planner_report["authority_impact"] == "operator"
    assert "operator-owned decision" in reason
    review_events = [event for event in events if event["type"] == "round.review.completed"]
    assert review_events[0]["operator_question"] == "请选择 A 或 B"
    assert review_events[0]["operator_options"][0]["id"] == "route-a"


def test_forbidden_engineer_question_becomes_autonomous_continuation(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Git is not available in the current shell.\n"
                "NEXT_OWNER=operator\n"
                "OPERATOR_QUESTION=Can you provide Git?"
            ),
            thread_id="t1",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Found the available Git binary and completed the work.\n"
            "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer",
            thread_id="t2",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete and review the work",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert [record.review.operator_question for record in rounds] == ["", ""]
    assert rounds[0].review.status == "continue"
    assert rounds[0].review.operator_options == []
    round_two_prompt = next(
        prompt for label, prompt, _options in backend.history if label == "engineer-r2"
    )
    assert "solve reversible environment, tool, worktree" in round_two_prompt


def test_inflight_forbid_takes_effect_at_engineer_boundary(tmp_path: Path) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    set_active_manager_directive(
        tmp_path,
        "questions are currently allowed",
        operator_question_policy="allow",
    )
    backend = MemoryBackend()

    def forbid_during_turn(_prompt, _options):
        set_active_manager_directive(
            tmp_path,
            "do not ask further questions",
            operator_question_policy="forbid",
        )
        return "NEXT_OWNER=operator\nOPERATOR_QUESTION=Can you configure Git?"

    backend.queue(
        "engineer-r1",
        CannedResponse(message_factory=forbid_during_turn, thread_id="t1"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Configured the available Git and finished.\n"
            "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer",
            thread_id="t2",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish without waiting",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_question_policy_root=tmp_path,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert rounds[0].review.status == "continue"
    assert all(not record.review.operator_question for record in rounds)


def test_inflight_allow_reenables_engineer_question_boundary(tmp_path: Path) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    set_active_manager_directive(
        tmp_path,
        "do not ask questions",
        operator_question_policy="forbid",
    )
    backend = MemoryBackend()

    def allow_during_turn(_prompt, _options):
        set_active_manager_directive(
            tmp_path,
            "questions are allowed again",
            operator_question_policy="allow",
        )
        return "NEXT_OWNER=operator\nOPERATOR_QUESTION=Choose A or B."

    backend.queue(
        "engineer-r1",
        CannedResponse(message_factory=allow_during_turn, thread_id="t1"),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="use the operator-selected route",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "Do it.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_question_policy_root=tmp_path,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert rounds[0].review.operator_question == "Choose A or B."


def test_forbidden_reviewer_question_preserves_telemetry_and_continues(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Prepared the change.\nNEXT_OWNER=reviewer",
            thread_id="t1",
        ),
    )
    backend.queue(
        "reviewer",
        CannedResponse(
            message=json.dumps({
                "status": "blocked",
                "reason": "The environment needs repair.",
                "next_action": "Ask the operator.",
                "operator_question": "Can you repair the environment?",
            }),
            input_tokens=17,
            output_tokens=9,
            thread_id="v1",
        ),
        CannedResponse(message=_done_review(), thread_id="v2"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Repaired the environment and verified the change.\n"
            "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer",
            thread_id="t2",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete and review the work",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    enforced = rounds[0].review
    assert enforced.status == "continue"
    assert enforced.operator_question == ""
    assert enforced.operator_options == []
    assert enforced.input_tokens == 17
    assert enforced.output_tokens == 9
    assert "solve reversible environment, tool, worktree" in enforced.next_action


def test_first_forbidden_question_at_hard_boundary_gets_one_retry(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message="Prepared a partial fix.\nNEXT_OWNER=reviewer"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(
            message=json.dumps({
                "status": "blocked",
                "reason": "A reversible tool issue remains.",
                "next_action": "Ask the operator.",
                "operator_question": "Can you install the missing tool?",
            })
        ),
        CannedResponse(message=_done_review()),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Resolved the tool issue autonomously.\n"
            "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer"
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete the fix",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            hard_escalate_rounds=1,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert rounds[0].review.status == "continue"
    assert "forward_progress" not in rounds[0].review.planner_report
    assert [label for label, _prompt, _options in backend.history].count(
        "engineer-r2"
    ) == 1


def test_policy_retry_preserves_existing_stall_clock(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for round_index in (1, 2, 3):
        backend.queue(
            f"engineer-r{round_index}",
            CannedResponse(
                message=f"Round {round_index} state preserved.\nNEXT_OWNER=reviewer"
            ),
        )
    backend.queue(
        "reviewer",
        CannedResponse(
            message=json.dumps({
                "status": "continue",
                "reason": "The route made no progress.",
                "next_action": "Try the next diagnostic.",
                "planner_report": {"forward_progress": False},
            })
        ),
        CannedResponse(
            message=json.dumps({
                "status": "blocked",
                "reason": "A reversible environment issue remains.",
                "next_action": "Ask the operator.",
                "operator_question": "Can you repair the environment?",
            })
        ),
        CannedResponse(
            message=json.dumps({
                "status": "continue",
                "reason": "The route still made no progress.",
                "next_action": "Use a different route.",
                "planner_report": {"forward_progress": False},
            })
        ),
    )
    events: list[dict] = []

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="resolve the blocker autonomously",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=4,
            no_progress_threshold=99,
            stall_threshold=2,
            hard_escalate_rounds=2,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "no_progress"
    assert len(rounds) == 3
    assert rounds[0].review.planner_report["forward_progress"] is False
    assert "forward_progress" not in rounds[1].review.planner_report
    assert rounds[2].review.planner_report["forward_progress"] is False
    stall_events = [event for event in events if event["type"] == "round.stall"]
    assert [event["semantic_stall_streak"] for event in stall_events] == [1, 2]


def test_repeated_forbidden_reviewer_question_ends_blocked_without_asking(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    for round_index in (1, 2):
        backend.queue(
            f"engineer-r{round_index}",
            CannedResponse(
                message="Preserved the current state.\nNEXT_OWNER=reviewer",
                thread_id=f"t{round_index}",
            ),
        )
    blocked_review = CannedResponse(
        message=json.dumps({
            "status": "blocked",
            "reason": "Irreversible authority is required.",
            "next_action": "Ask for authority.",
            "operator_question": "May I perform the irreversible action?",
        })
    )
    backend.queue("reviewer", blocked_review, blocked_review)

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="perform only authorized work",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert [record.review.status for record in rounds] == ["continue", "blocked"]
    assert all(not record.review.operator_question for record in rounds)
    assert rounds[-1].review.next_action == ""


class _StructuredBlockedEngineer:
    backend = "memory"

    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, **_kwargs) -> RunnerResult:
        self.calls += 1
        return RunnerResult(
            exit_code=0,
            agent_messages=["Blocked after preserving the worktree."],
            role_decisions=[{
                "role": "engineer",
                "payload": {
                    "status": "blocked",
                    "result": "Only unavailable credentials remain; state preserved.",
                    "next_owner": "reviewer",
                },
            }],
        )


class _NoReviewExpected:
    def evaluate(self, **_kwargs):
        raise AssertionError("independent review is disabled")


def test_no_review_structured_engineer_blocked_settles_immediately(
    tmp_path: Path,
) -> None:
    backend = _StructuredBlockedEngineer()
    engine = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=_NoReviewExpected(),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )

    status, rounds, _final, reason, _tid = engine.run(
        objective="perform credential-gated work",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "Do it.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            require_independent_review=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert backend.calls == 1
    assert len(rounds) == 1
    assert rounds[0].review.operator_question == ""
    assert reason == "Only unavailable credentials remain; state preserved."


def test_no_review_legacy_blocked_marker_settles_immediately(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Preserved the current worktree.\nMILESTONE_STATUS=blocked"
        ),
    )

    status, rounds, _final, reason, _tid = _engineer(backend).run(
        objective="perform credential-gated work",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "Do it.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            require_independent_review=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert len(rounds) == 1
    assert rounds[0].review.operator_question == ""
    assert reason == "Engineer reported an unresolved blocker."


def test_engineer_reviewer_request_enters_independent_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Completed the artifact and its check script.\n"
                "MILESTONE_STATUS=continue\n"
                "OPERATOR_QUESTION=Please invoke the independent hostile Reviewer "
                "to review this artifact.\n"
                "OPERATOR_OPTIONS=review :: Invoke hostile Reviewer :: "
                "Run the standard independent review."
            ),
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete and independently review the artifact",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "reviewer",
    ]
    assert status == "done"
    assert rounds[0].review.review_source == "reviewer"
    assert rounds[0].review.operator_question == ""


def test_structured_reviewer_handoff_does_not_override_real_authority(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "MILESTONE_STATUS=continue\n"
                "NEXT_OWNER=reviewer\n"
                "OPERATOR_QUESTION=Authorize the budget and external publication "
                "before review.\n"
                "OPERATOR_OPTIONS=approve :: Approve :: Spend budget and publish."
            ),
            thread_id="t1",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="publish an artifact",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert rounds[0].review.operator_question.startswith("Authorize the budget")


def test_explicit_operator_handoff_is_authoritative_for_reviewer_wording(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "MILESTONE_STATUS=continue\n"
                "NEXT_OWNER=operator\n"
                "OPERATOR_QUESTION=Please authorize invoking the independent Reviewer.\n"
                "OPERATOR_OPTIONS=approve :: Approve :: Grant authorization."
            ),
            thread_id="t1",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="perform an authorization-gated review",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert rounds[0].review.operator_question.startswith("Please authorize")


def test_legacy_reviewer_wording_does_not_bypass_operator_approval(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "MILESTONE_STATUS=continue\n"
                "OPERATOR_QUESTION=Request the independent Reviewer after operator approval.\n"
                "OPERATOR_OPTIONS=approve :: Approve review :: Grant approval."
            ),
            thread_id="t1",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="perform an approval-gated review",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert rounds[0].review.operator_question.startswith("Request the independent")


def test_structured_engineer_handoff_continues_without_early_review(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Completed the first internal step.\n"
                "MILESTONE_STATUS=continue\n"
                "NEXT_OWNER=engineer\n"
                "OPERATOR_QUESTION=none"
            ),
            thread_id="t1",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message=(
                "Completed the artifact.\n"
                "MILESTONE_STATUS=done\n"
                "NEXT_OWNER=reviewer\n"
                "OPERATOR_QUESTION=none"
            ),
            thread_id="t2",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete a two-step artifact",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
        "reviewer",
    ]
    assert status == "done"
    assert len(rounds) == 1
