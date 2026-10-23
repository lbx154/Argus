"""The baton line: one closing ``→ <holder>`` line, still read for the roles not yet on ``hand_off``.

docs/role-handoff-design.md, "The baton". It is the only handoff read: the
older ``NEXT_OWNER=`` footer is not.
"""
from __future__ import annotations

import pytest

from argus.core.role_handoff import Handoff, listed_choices, read_baton_line, strip_handoff

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
    "Work.\n→ operator:",
    "→ operator:\n- Keep it\n- Switch",
    "Decision:\n→ operator:\n- Keep it\n- Switch",
    "Work.\n→ wait: `job-42`",
    "Work.\n→ 等待: `job-42`",
])
def test_an_unreadable_or_conflicting_line_names_no_one(text: str) -> None:
    assert read_baton_line(text) is None


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


def test_the_same_list_reads_under_any_question() -> None:
    assert listed_choices("DOMAIN_QUESTION=Which?\n- Keep it\n\n2. Switch") == ("Keep it", "Switch")
    assert listed_choices("DOMAIN_QUESTION=Keep it or switch?") == ()


def test_a_list_under_another_holder_is_prose_not_options() -> None:
    assert read_baton_line("Built it.\n→ reviewer\n- tests pass") == Handoff("reviewer")
    assert strip_handoff("Built it.\n→ reviewer\n- tests pass") == "Built it.\n- tests pass"


def test_a_person_reads_the_prose_without_the_protocol() -> None:
    assert strip_handoff("Built it.\n→ reviewer") == "Built it."
    assert strip_handoff("Prose.\nDecision:\nRESULT=Parser fixed.\n→ reviewer") == "Prose.\nParser fixed."
    # Trial s-6f0e6213: the Manager's stage verdict read "… ACTION=hold TARGET_STAGE=delivery REASON=…".
    assert strip_handoff(
        "**暂不能交付。** 还没有实现。\n\nDecision:\nACTION=hold\nTARGET_STAGE=delivery\nREASON=实现未完成"
    ) == "**暂不能交付。** 还没有实现。"
