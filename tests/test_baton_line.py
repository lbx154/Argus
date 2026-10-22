"""The baton line: one closing ``→ <holder>`` line routes a turn.

docs/role-handoff-design.md, "The baton". The Engineer's and the Reviewer's
line is where the work goes; the host never sends it elsewhere. It is the only
handoff read: the older ``NEXT_OWNER=`` footer is not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.role_handoff import Handoff, read_baton_line, strip_handoff
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.roles.prompts import engineer as engineer_prompts


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


@pytest.mark.parametrize("text, handoff", [
    ("Did the work.\n→ reviewer", Handoff("reviewer")),
    ("Did the work.\n→ engineer", Handoff("engineer")),
    ("Did the work.\n→ operator: Spend $40 on the API?", Handoff("operator", "Spend $40 on the API?")),
    ("Fixed.\n→ done", Handoff("done")),
    ("Fixed.\nDecision:\n→ planner", Handoff("planner")),
])
def test_the_line_names_the_holder(text: str, handoff: Handoff) -> None:
    assert read_baton_line(text) == handoff


@pytest.mark.parametrize("text", [
    "Fixed.\nDecision:\nMILESTONE_STATUS=done",
    "Did the work.\nDecision:\nNEXT_OWNER=operator\nOPERATOR_QUESTION=Spend $40?\nOPERATOR_NEED=spending",
])
def test_the_old_footer_is_not_read(text: str) -> None:
    assert read_baton_line(text) is None
    assert read_baton_line("Work.\n→ engineer\nNEXT_OWNER=reviewer") == Handoff("engineer")


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
])
def test_decoration_case_and_the_operator_facing_names_are_read(line: str, owner: str) -> None:
    handoff = read_baton_line(f"Did the work.\n{line}")
    assert handoff is not None and handoff.holder == owner


@pytest.mark.parametrize("text", [
    "Work.\n→ qa",
    "Work.\n→ reviewer\n→ engineer",
    "Work.\n→ done\n→ engineer",
    "Work.\n→ operator",
    "Work.\n→ wait: `job-42`",
    "Work.\n→ 等待: `job-42`",
])
def test_an_unreadable_or_conflicting_line_names_no_one(text: str) -> None:
    assert read_baton_line(text) is None


@pytest.mark.parametrize("message", ["Fixed.\nDecision:\nMILESTONE_STATUS=done", "Fixed.\n→ qa"])
def test_an_unreadable_line_goes_to_the_reviewer_never_done(tmp_path: Path, message: str) -> None:
    result, _rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message=message)),
        ("reviewer", CannedResponse(review_action=("submit_review", {"review": "Holds.\n→ done"}))),
        review=False,
    )
    assert (result, labels) == ("done", ["engineer-r1", "reviewer"])


def test_an_arrow_quoted_from_the_task_does_not_route() -> None:
    above_decision = "The task text reads:\n→ done\nI changed the parser.\nDecision:\nStill open."
    assert read_baton_line(above_decision) is None
    far_from_the_end = "→ done\n" + "\n".join(f"Step {index}." for index in range(5))
    assert read_baton_line(far_from_the_end) is None
    assert read_baton_line("I changed the parser.\n-> next I will profile it") is None


@pytest.mark.parametrize("text, options", [
    ("Two parsers fit.\n→ operator: Keep the old date format?\n- Keep it\n- Switch to ISO",
     ("Keep it", "Switch to ISO")),
    ("Two parsers fit.\nDecision:\n→ operator: Keep it?\n\n1. Keep it\n2) Switch", ("Keep it", "Switch")),
    ("Two parsers fit.\n→ operator: Keep it?\n* **Keep it**\n• Switch", ("**Keep it**", "Switch")),
    # Choices do not count toward the closing five lines.
    ("Two parsers fit.\n→ operator: Which?\n" + "\n".join(f"- Parser {n}" for n in range(6)),
     tuple(f"Parser {n}" for n in range(6))),
])
def test_an_operator_question_lists_its_options_under_the_line(text: str, options: tuple[str, ...]) -> None:
    handoff = read_baton_line(text)
    assert handoff is not None and handoff.holder == "operator"
    assert handoff.options == options
    assert strip_handoff(text) == "Two parsers fit."


@pytest.mark.parametrize("text", [
    "Two parsers fit.\n→ operator: Keep the old date format?",
    "Two parsers fit.\n→ operator: Keep the old date format?\n- Keep it\nThanks for reading.",
    "Two parsers fit.\n→ operator: Keep the old date format?\nKeep it / Switch to ISO",
    "Two parsers fit.\n- Keep it\n- Switch to ISO\n→ operator: Keep the old date format?",
])
def test_a_list_that_cannot_be_read_as_options_leaves_the_question_standing(text: str) -> None:
    assert read_baton_line(text) == Handoff("operator", "Keep the old date format?")


def test_a_list_under_another_holder_is_prose_not_options() -> None:
    assert read_baton_line("Built it.\n→ reviewer\n- tests pass") == Handoff("reviewer")
    assert strip_handoff("Built it.\n→ reviewer\n- tests pass") == "Built it.\n- tests pass"


def test_a_person_reads_the_prose_without_the_protocol() -> None:
    assert strip_handoff("Built it.\n→ reviewer") == "Built it."
    assert strip_handoff(
        'Prose.\nARGUS_ROLE_DECISION={"role":"planner","payload":{}}\nDecision:\nRESULT=Parser fixed.\n→ reviewer'
    ) == "Prose.\nParser fixed."


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


def test_done_under_required_review_still_reaches_the_reviewer(tmp_path: Path) -> None:
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message="Fixed the parser; its tests pass.\n→ done")),
        ("reviewer", CannedResponse(review_action=("submit_review", {"review": "Holds.\n→ done"}))),
    )
    assert labels == ["engineer-r1", "reviewer"]
    assert result == "done"
    assert rounds[0].review.review_source == "reviewer"


def test_an_operator_question_parks_however_it_is_worded(tmp_path: Path) -> None:
    """The Engineer named the operator; no word list sends it to the Reviewer instead."""
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message="Two parsers fit.\n→ operator: Keep the old date format?")),
    )
    assert labels == ["engineer-r1"]
    assert result == "blocked"
    review = rounds[0].review
    assert (review.status, review.review_source) == ("blocked", "engineer_handoff")
    assert review.operator_question == "Keep the old date format?"
    assert review.operator_options == []


def test_the_engineer_offers_the_operator_options(tmp_path: Path) -> None:
    _result, rounds, _labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(
            message="Two parsers fit.\n→ operator: Keep the old date format?\n- Keep it\n- Switch to ISO",
        )),
    )
    review = rounds[0].review
    assert review.reason == "Two parsers fit."
    assert [(option["id"], option["label"]) for option in review.operator_options] == [
        ("option-1", "Keep it"), ("option-2", "Switch to ISO"),
    ]


@pytest.mark.parametrize("message", ["Built the parser.\n→ reviewer", "Built the parser."])
def test_the_reviewer_is_reached_even_where_review_is_optional(tmp_path: Path, message: str) -> None:
    """Optional review only lets the Engineer accept; it never turns a handoff back."""
    result, _rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message=message)),
        ("reviewer", CannedResponse(review_action=("submit_review", {"review": "Holds.\n→ done"}))),
        review=False,
    )
    assert (result, labels) == ("done", ["engineer-r1", "reviewer"])


@pytest.mark.parametrize("line, status, question", [
    ("→ planner", "replan_requested", ""),
    ("→ manager", "blocked", ""),
    ("→ operator: Which licence may the dataset use?", "blocked", "Which licence may the dataset use?"),
    ("→ operator: Which licence may the dataset use?\n- MIT\n- CC-BY", "blocked",
     "Which licence may the dataset use?"),
])
def test_the_reviewer_hands_the_work_on_with_the_same_line(
    tmp_path: Path, line: str, status: str, question: str,
) -> None:
    result, rounds, labels = _run(
        tmp_path,
        ("engineer-r1", CannedResponse(message="Built the parser.\n→ reviewer")),
        ("reviewer", CannedResponse(review_action=(
            "submit_review", {"review": f"The criteria contradict each other.\n{line}"},
        ))),
    )
    assert labels == ["engineer-r1", "reviewer"]
    assert result == status
    review = rounds[0].review
    assert (review.status, review.review_source, review.operator_question) == (status, "reviewer", question)
    assert review.reason == "The criteria contradict each other."
    assert [option["label"] for option in review.operator_options] == ["MIT", "CC-BY"] * ("- MIT" in line)


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
        "paths, the decisive check, what is open, why the next holder. Its first "
        "sentence is what the operator reads: what was done and found, in plain words. "
        "Then one line naming that holder:\n"
        "→ reviewer\n"
        "Or `→ done` (milestone complete), `→ engineer` (keep working), `→ planner` "
        "(task mis-scoped), `→ manager` (obstacle only it can remove), or "
        "`→ operator: <one question>`. When the operator picks among a few answers, "
        "list each under `→ operator:` as `- <answer>`. The Reviewer you hand to with "
        "`→ reviewer` does the independent review; do not start a review of your own."
    )


@pytest.mark.parametrize("operator", [True, False])
@pytest.mark.parametrize("include_static,compact_team", [(True, False), (False, False), (True, True)])
def test_every_engineer_prompt_asks_for_the_line_and_no_footer(
    monkeypatch: pytest.MonkeyPatch, include_static: bool, compact_team: bool, operator: bool,
) -> None:
    monkeypatch.setattr("argus.core.autonomy.operator_available", lambda: operator)
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
    for legacy in (
        "MILESTONE_STATUS", "NEXT_OWNER", "next_owner", "OPERATOR_NEED", "OPERATOR_OPTIONS",
        "ASSUMPTION", "id::label", "wait_for", "Decision:",
    ):
        assert legacy not in prompt
