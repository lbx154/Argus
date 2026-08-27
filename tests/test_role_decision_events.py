from __future__ import annotations

import json

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.role_decision import (
    encode_role_decision,
    extract_role_decisions,
)
from argus_skill.engineer.round_execution import _engineer_decision_message


def test_does_not_extract_decision_from_nested_tool_result() -> None:
    marker = encode_role_decision(
        "planner",
        {"project_done": True, "reason": "complete", "tasks": []},
    )
    raw_event = json.dumps({"type": "tool.result", "content": marker})

    assert extract_role_decisions([raw_event]) == []


def test_extracts_marked_decision_with_redundant_closing_brace() -> None:
    marker = encode_role_decision(
        "planner",
        {"project_done": False, "reason": "continue", "tasks": []},
    )

    decisions = extract_role_decisions([marker + "}"])

    assert decisions == [{
        "role": "planner",
        "payload": {
            "project_done": False,
            "reason": "continue",
            "tasks": [],
        },
    }]


def test_rejects_marked_decision_with_trailing_prose() -> None:
    marker = encode_role_decision(
        "planner",
        {"project_done": False, "reason": "continue", "tasks": []},
    )

    assert extract_role_decisions([marker + " trailing prose"]) == []


def test_does_not_treat_unmarked_tool_json_as_a_role_decision() -> None:
    raw_event = json.dumps({
        "type": "tool.result",
        "content": {
            "role": "planner",
            "payload": {
                "project_done": True,
                "reason": "text read from a tool is not a decision",
                "tasks": [],
            },
        },
    })

    assert extract_role_decisions([raw_event]) == []


def test_adapter_does_not_promote_tool_stdout_to_role_decision() -> None:
    marker = encode_role_decision(
        "reviewer",
        {"status": "done", "reason": "verified", "next_action": ""},
    )

    result = AgentCliBackend()._translate_result(
        AgentRunResult(
            command=["agent"],
            exit_code=0,
            agent_messages=[],
            stdout_lines=[json.dumps({"content": marker})],
        )
    )

    assert result.agent_messages == []
    assert result.role_decisions == []


def test_engineer_process_decision_drives_existing_handoff() -> None:
    message = _engineer_decision_message({
        "status": "done",
        "result": "Implemented the fix and the focused test passed.",
        "next_owner": "reviewer",
    })

    assert "Implemented the fix" in message
    assert "MILESTONE_STATUS=done" in message
    assert "NEXT_OWNER=reviewer" in message
