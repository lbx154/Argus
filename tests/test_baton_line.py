"""The baton line: one closing ``→ <holder>`` line routes an Engineer's turn.

docs/role-handoff-design.md, "The baton". The older ``NEXT_OWNER=`` footer is
still read where no line is present, so each case pins the line against the
footer it replaces, or a reading the round loop now relies on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import RunnerResult
from argus.core.role_handoff import parse_engineer_handoff, read_baton_line, strip_handoff
from argus.engineer.external_work import parse_external_wait_request
from argus.engineer.round_self_review import _milestone_is_done
from argus.engineer.round_state import EngineerTurnOutcome
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer._parsing import decision_from_deferred_wait
from argus.roles.prompts import engineer as engineer_prompts


def _outcome(message: str) -> EngineerTurnOutcome:
    return EngineerTurnOutcome(
        engineer_result=RunnerResult(exit_code=0, agent_messages=[message]),
        round_thread_id=None, fatal_error=None, safe_fatal_error=None, stop_kind=None,
        raw_engineer_message=message, engineer_message=message,
        process_ownership_note="", round_started_at=0.0,
    )


def _run(tmp_path: Path, *responses: tuple[str, CannedResponse], review: bool = True):
    backend = MemoryBackend()
    for label, response in responses:
        backend.queue(label, response)
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    status, rounds, _final, _reason, _tid = engineer.run(
        objective="repair the parser",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=review),
        workdir=tmp_path,
    )
    return status, rounds, [label for label, _prompt, _options in backend.history]


# --- Reading the line -------------------------------------------------------


@pytest.mark.parametrize("footer, line", [
    ("NEXT_OWNER=reviewer", "→ reviewer"),
    ("NEXT_OWNER=engineer", "→ engineer"),
    (
        "NEXT_OWNER=operator\nOPERATOR_QUESTION=Spend $40 on the API?\nOPERATOR_NEED=spending",
        "→ operator: Spend $40 on the API?\nOPERATOR_NEED=spending",
    ),
])
def test_a_baton_line_reads_as_the_footer_it_replaces(footer: str, line: str) -> None:
    old = parse_engineer_handoff(f"Did the work.\nDecision:\n{footer}")
    new = parse_engineer_handoff(f"Did the work.\n{line}")
    assert (new.next_owner, new.operator_question, new.operator_need, new.source) == (
        old.next_owner, old.operator_question, old.operator_need, old.source,
    )


def test_done_reads_the_same_as_the_footer_did() -> None:
    assert _milestone_is_done(_outcome("Fixed.\nDecision:\nMILESTONE_STATUS=done"))
    assert _milestone_is_done(_outcome("Fixed.\n→ done"))
    assert not _milestone_is_done(_outcome("Fixed.\n→ reviewer"))


@pytest.mark.parametrize("line, owner", [
    ("- → reviewer", "reviewer"),
    ("**→ engineer**", "engineer"),
    ("`→ planner`", "planner"),
    ("-> manager", "manager"),
    ("=> done", "done"),
    ("→ `Reviewer`", "reviewer"),
    ("> → done", "done"),
    ("→ 复核", "reviewer"),
    ("→ 执行", "engineer"),
    ("→ 规划", "planner"),
    ("→ 统筹", "manager"),
    ("→ 完成", "done"),
    ("→ 操作员：要不要发布？", "operator"),
    ("→ 等待: `job-42`", "wait"),
])
def test_decoration_case_and_the_operator_facing_names_are_read(line: str, owner: str) -> None:
    handoff = read_baton_line(f"Did the work.\n{line}")
    assert handoff is not None and (handoff.next_owner, handoff.source) == (owner, "structured")


@pytest.mark.parametrize("text", [
    "Work.\n→ qa",
    "Work.\n→ reviewer\n→ engineer",
    "Work.\n→ done\n→ engineer",
    "Work.\n→ operator",
    "Work.\n→ wait:",
    "Work.\n→ engineer\nNEXT_OWNER=reviewer",
])
def test_an_unreadable_or_conflicting_line_falls_back_to_the_reviewer(text: str) -> None:
    handoff = read_baton_line(text)
    assert handoff is not None and (handoff.next_owner, handoff.source) == ("reviewer", "default")
    assert not _milestone_is_done(_outcome(text))


def test_an_arrow_quoted_from_the_task_does_not_route() -> None:
    above_decision = "The task text reads:\n→ done\nI changed the parser.\nDecision:\nNEXT_OWNER=engineer"
    assert parse_engineer_handoff(above_decision).next_owner == "engineer"
    far_from_the_end = "→ done\n" + "\n".join(f"Step {index}." for index in range(5))
    assert read_baton_line(far_from_the_end) is None
    assert read_baton_line("I changed the parser.\n-> next I will profile it") is None


def test_a_wait_line_names_the_job_the_json_line_did() -> None:
    json_line = parse_external_wait_request('Launched.\n{"wait_for":"subagent","wait_id":"job-42"}')
    assert json_line == ("subagent", "job-42")
    for text in (
        "Launched.\n→ wait: job-42",
        "Launched.\n→ wait: `job-42`",
        "Launched.\n→ wait: the run `job-42` finishes",
    ):
        assert parse_external_wait_request(text) == json_line
    assert parse_external_wait_request("Launched.\n→ wait: the training run finishes") is None


def test_a_person_reads_the_prose_without_the_protocol() -> None:
    assert strip_handoff("Built it.\n→ reviewer") == "Built it."
    assert strip_handoff(
        "Prose.\nDecision:\nMILESTONE_STATUS=done\nRESULT=Parser fixed.\nNEXT_OWNER=reviewer"
    ) == "Prose.\nParser fixed."


def test_a_review_that_closes_on_a_wait_line_defers_to_that_run() -> None:
    review = decision_from_deferred_wait(["Every clause depends on the benchmark.\n→ wait: `bench-1`"])
    assert review is not None and review.status == "continue"
    assert review.reason == "Every clause depends on the benchmark."
    assert "bench-1" in review.next_action


# --- Routing ----------------------------------------------------------------


@pytest.mark.parametrize("line, status", [
    ("→ planner", "replan_requested"),
    ("→ manager", "blocked"),
])
def test_the_engineer_hands_straight_to_the_planner_or_the_manager(
    tmp_path: Path, line: str, status: str,
) -> None:
    """Neither accepts work, so no review stands between the Engineer and them."""
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message=f"The task assumes a GPU this host lacks.\n{line}")),
    )
    assert labels == ["engineer-r1"]
    assert result == status
    review = rounds[0].review
    assert (review.status, review.review_source) == (status, "engineer_handoff")
    assert review.reason == "The task assumes a GPU this host lacks."
    assert review.planner_report.get("manager_attention") == (
        "needed" if line == "→ manager" else None
    )


def test_done_under_required_review_still_reaches_the_reviewer(tmp_path: Path) -> None:
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message="Fixed the parser; its tests pass.\n→ done")),
        ("reviewer", CannedResponse(review_action=("approve_review", {"review": "Holds."}))),
    )
    assert labels == ["engineer-r1", "reviewer"]
    assert result == "done"
    assert rounds[0].review.review_source == "reviewer"


def test_done_without_required_review_settles_on_the_line(tmp_path: Path) -> None:
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message="Fixed the parser; its tests pass.\n→ done")),
        review=False,
    )
    assert (result, labels) == ("done", ["engineer-r1"])
    assert rounds[0].review.review_source == "engineer_self_review"


# --- The prompt ---------------------------------------------------------------


def test_the_engineer_is_asked_for_the_baton_line() -> None:
    assert engineer_prompts._HANDOFF_RULE == (
        "## Handing off\n"
        "End with a short handoff in the operator's language: changes with output "
        "paths, the decisive check, what is open, why the next holder. Then one line "
        "naming that holder:\n"
        "→ reviewer\n"
        "Or `→ done` (milestone complete), `→ engineer` (keep working), `→ planner` "
        "(task mis-scoped), `→ manager` (obstacle only it can remove), "
        "`→ operator: <one question>`, or `→ wait: <task-id>` (submitted job)."
    )


@pytest.mark.parametrize("include_static,compact_team", [(True, False), (False, False), (True, True)])
def test_every_engineer_prompt_asks_for_the_line_and_no_footer(
    monkeypatch: pytest.MonkeyPatch, include_static: bool, compact_team: bool,
) -> None:
    monkeypatch.setattr(engineer_prompts, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer_prompts, "native_shell_summary", lambda: "")
    prompt = engineer_prompts.build_mission_prompt(
        task="Repair the parser.",
        skill_text="",
        next_action=None if include_static else "Handle empty input.",
        include_static=include_static,
        compact_team=compact_team,
    )
    assert prompt.count(engineer_prompts._HANDOFF_RULE) == 1
    for legacy in ("MILESTONE_STATUS", "NEXT_OWNER", "next_owner", "wait_for", "Decision:"):
        assert legacy not in prompt
