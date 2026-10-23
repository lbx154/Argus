"""The baton line: one closing ``→ <holder>`` line, still read for the roles not yet on ``hand_off``.

docs/role-handoff-design.md, "The baton". It is the only handoff read: the
older ``NEXT_OWNER=`` footer is not.
"""
from __future__ import annotations

from argus.core.role_handoff import listed_choices, strip_handoff

# --- Reading the line -------------------------------------------------------


def test_the_same_list_reads_under_any_question() -> None:
    assert listed_choices("DOMAIN_QUESTION=Which?\n- Keep it\n\n2. Switch") == ("Keep it", "Switch")
    assert listed_choices("DOMAIN_QUESTION=Keep it or switch?") == ()


def test_a_person_reads_the_prose_without_the_protocol() -> None:
    assert strip_handoff("Built it.\n→ reviewer") == "Built it."
    assert strip_handoff("Prose.\nDecision:\nRESULT=Parser fixed.\n→ reviewer") == "Prose.\nParser fixed."
    # Trial s-6f0e6213: the Manager's stage verdict read "… ACTION=hold TARGET_STAGE=delivery REASON=…".
    assert strip_handoff(
        "**暂不能交付。** 还没有实现。\n\nDecision:\nACTION=hold\nTARGET_STAGE=delivery\nREASON=实现未完成"
    ) == "**暂不能交付。** 还没有实现。"
