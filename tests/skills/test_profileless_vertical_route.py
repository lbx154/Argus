"""A profile sent for a vertical that declares no workflow profiles is inert.

Regression: a fresh daemon died at boot because the Manager route picked a
profile-less vertical while still naming a workflow profile, and finalize
rejected the whole route with no retry.
"""
from __future__ import annotations

import json
from types import ModuleType, SimpleNamespace

import pytest

from argus.core.pipeline_state import read_pipeline_state
from argus.manager import Manager
from argus.manager.domain_author import VerticalDecisionError
from argus.skills.stage_machine import ChecklistItem
from argus.verticals import _registry


def _module(name, *, profiles):
    module = ModuleType(f"{name}.stages")
    module.ARGUS_VERTICAL_API_VERSION = 1
    module.VERTICAL_PURPOSE = "A scoped test capability"
    module.STAGE_ORDER = module.CHECKLIST_STAGE_ORDER = ("design", "verify")
    module.CHECKLIST_ITEMS = {
        stage: (ChecklistItem(f"{stage}.evidence", f"{stage} evidence", f"{stage}.txt"),)
        for stage in module.STAGE_ORDER
    }
    module.completion_gate = "none"
    if profiles:
        module.WORKFLOW_PROFILES = {
            "full": {"purpose": "complete delivery", "stages": module.STAGE_ORDER},
        }
    return module


@pytest.fixture
def verticals(monkeypatch):
    modules = {
        "plain_lab": _module("plain_lab", profiles=False),
        "profile_lab": _module("profile_lab", profiles=True),
    }
    entries = [
        SimpleNamespace(name=name, value=f"{name}.stages", load=lambda m=module: m)
        for name, module in modules.items()
    ]
    monkeypatch.setattr(_registry, "entry_points", lambda group: entries)
    _registry.refresh_vertical_plugins()
    yield modules
    _registry.refresh_vertical_plugins()


class RouteRunner:
    def __init__(self, vertical, **extra):
        self.decision = {
            "choice": "existing", "vertical": vertical, "workflow_mode": "staged",
            "confidence": 0.99, "execution_task": "Compare four models.",
            **extra,
        }

    def run_exec(self, *, prompt, **kwargs):
        message = json.dumps(self.decision)
        return SimpleNamespace(
            last_agent_message=message, agent_messages=[message],
            thread_id="route-test", tool_activity_observed=True,
        )


@pytest.mark.parametrize("fast", ["0", "1"])
@pytest.mark.parametrize("extra", [
    {"workflow_profile": "full"},
    {"workflow_profile": "custom", "workflow_requested_stages": ["verify"]},
])
def test_profile_for_profileless_vertical_is_dropped_not_fatal(
    verticals, tmp_path, monkeypatch, fast, extra,
):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", fast)
    manager = Manager(project_root=tmp_path, runner=RouteRunner("plain_lab", **extra))
    decision = manager.decide_vertical("Compare four models.")
    assert decision.vertical == "plain_lab"
    assert decision.workflow_profile == ""
    assert decision.workflow_requested_stages == ()
    assert "provides no workflow profiles" in decision.adaptation_reason
    division = manager.commit_vertical_decision("Compare four models.", decision)
    assert division.stages == ["design", "verify"]
    assert "workflow_profile" not in read_pipeline_state(tmp_path)


def test_invalid_profile_on_profiled_vertical_still_fails(verticals, tmp_path):
    manager = Manager(
        project_root=tmp_path,
        runner=RouteRunner("profile_lab", workflow_profile="invented"),
    )
    with pytest.raises(VerticalDecisionError, match="invalid workflow_profile"):
        manager.decide_vertical("Compare four models.")
    assert not read_pipeline_state(tmp_path)
