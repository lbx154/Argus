from __future__ import annotations

from argus_skill.engineer.external_work import parse_external_wait_request


def test_subagent_wait_uses_structured_request() -> None:
    assert parse_external_wait_request(
        '{"wait_for": "subagent", "wait_id": "task-123"}'
    ) == ("subagent", "task-123")


def test_external_work_wait_uses_structured_request() -> None:
    assert parse_external_wait_request(
        '{"wait_for": "external_work", "wait_id": "work-123"}'
    ) == ("external_work", "work-123")


def test_incomplete_json_is_not_a_wait_request() -> None:
    assert parse_external_wait_request('"wait_for": "subagent"') is None
