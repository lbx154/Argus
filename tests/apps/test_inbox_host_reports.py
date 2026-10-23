"""Reports Argus writes about its own background work are not operator messages."""
from __future__ import annotations

import pytest

from argus.apps._inbox import queue_inbox_message
from argus.apps._inbox_delivery import DurableInboxReceiver
from argus.core.operator_context import OperatorContextStore
from argus.skills.vertical_select import persist_vertical

REPORT = "## Subagent Report: roofline-benchmark-gpu1-v2\n**Event**: COMPLETED\n\n- exit_code: 0\n"
# Trial, 2026-10-21: the note that an answer resumed its task went through
# the front door twice (a STEER, then its authorization check) after the
# answer had already been applied.
ANSWERED = "[MANAGER OPERATOR-ANSWER DECISION] Task t-1 was answered and resumes with this decision: A."


@pytest.mark.parametrize(("source", "report"), [("subagent", REPORT), ("manager.answer", ANSWERED)])
def test_host_reports_are_read_once_without_the_front_door(tmp_path, monkeypatch, source, report):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    life = tmp_path / "life"
    life.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software", workflow_mode="direct")
    queue_inbox_message(life, report, source=source)
    queue_inbox_message(life, "Please also plot the residuals.", source="operator")
    receiver = DurableInboxReceiver(life, consumer="supervisor", project_root=project)
    first = receiver.receive(mission_id="", limit=10)
    assert [str(text) for text in first] == [report.strip()]
    receiver.settle(first)
    second = receiver.receive(mission_id="", limit=10)
    assert [str(text) for text in second] == ["Please also plot the residuals."]
    # The inbox classifies nothing: the operator's words reach the round as guidance.
    # Nothing about the report became durable operator context.
    assert not [
        record for record in OperatorContextStore(life).records()
        if report.strip() in str(getattr(record, "text", ""))
    ]
    receiver.settle(second)
