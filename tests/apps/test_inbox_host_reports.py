"""Reports Argus writes about its own background work are not operator messages."""
from __future__ import annotations

from types import SimpleNamespace

from argus.apps._inbox import queue_inbox_message
from argus.apps._inbox_delivery import DurableInboxReceiver
from argus.core.operator_context import OperatorContextStore
from argus.skills.vertical_select import persist_vertical

REPORT = "## Subagent Report: roofline-benchmark-gpu1-v2\n**Event**: COMPLETED\n\n- exit_code: 0\n"


def test_subagent_reports_are_read_once_without_the_front_door(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    life = tmp_path / "life"
    life.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software", workflow_mode="direct")
    queue_inbox_message(life, REPORT, source="subagent")
    queue_inbox_message(life, "Please also plot the residuals.", source="operator")
    classified: list[str] = []

    def classify(text, *, intake_sink, **kwargs):
        classified.append(text)
        intake_sink({"kind": "ephemeral"})

    receiver = DurableInboxReceiver(life, consumer="supervisor", project_root=project)
    manager = SimpleNamespace(classify_front_door=classify)
    first = receiver.receive(manager=manager, mission_id="", limit=10)
    assert [str(text) for text in first] == [REPORT.strip()]
    assert first[0].ephemeral and classified == []
    receiver.settle(first)
    second = receiver.receive(manager=manager, mission_id="", limit=10)
    assert [str(text) for text in second] == ["Please also plot the residuals."]
    # Only the operator's own words went through the Manager's front door.
    assert classified == ["Please also plot the residuals."]
    # Nothing about the report became durable operator context.
    assert not [
        record for record in OperatorContextStore(life).records()
        if "Subagent Report" in str(getattr(record, "text", ""))
    ]
    receiver.settle(second)
