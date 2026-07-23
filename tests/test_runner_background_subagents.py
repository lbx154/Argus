from __future__ import annotations

from argus_skill.engineer.background_subagents import parse_wait_sentinel
from argus_skill.engineer.external_work import parse_external_wait_sentinel


def test_subagent_wait_uses_plain_text_sentinel() -> None:
    assert parse_wait_sentinel("WAIT_FOR_SUBAGENT: task-123") == "task-123"


def test_external_work_wait_uses_plain_text_sentinel() -> None:
    assert (
        parse_external_wait_sentinel("WAIT_FOR_EXTERNAL_WORK: work-123")
        == "work-123"
    )


def test_json_control_snippet_is_not_a_wait_request() -> None:
    message = '"wait_for": "subagent", "wait_id": "task-123"'
    assert parse_wait_sentinel(message) is None
    assert parse_external_wait_sentinel(message) is None
