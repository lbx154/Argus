"""A saved Planner setting must reach the next lifetime Planner call."""

from types import SimpleNamespace

import pytest

from argus_skill.core.knob_store import write_persisted_knobs
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor


@pytest.mark.parametrize(("persisted", "environment", "expected"), [
    ("xhigh", None, "xhigh"),
    ("xhigh", "", "xhigh"),
    ("xhigh", "   ", "xhigh"),
    ("xhigh", "medium", "medium"),
    (None, None, "high"),
    (None, "", "high"),
    (None, "   ", "high"),
])
def test_planner_effort_uses_environment_then_persisted_then_default(
    tmp_path, monkeypatch, persisted, environment, expected,
):
    name = "ARGUS_SKILL_PLANNER_REASONING_EFFORT"
    settings = {"ARGUS_SKILL_MODEL": "gpt-6-astra"}
    if persisted is not None:
        settings[name] = persisted
    write_persisted_knobs(settings)
    if environment is not None:
        monkeypatch.setenv(name, environment)
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"),
        runner=SimpleNamespace(), sink=SimpleNamespace(handle_event=lambda event: None),
    )
    config = supervisor._planner_config()
    assert config.reasoning_effort == expected
    assert config.model == "gpt-6-astra"
    assert supervisor.engineer_model == "gpt-5.5"
    assert supervisor.reviewer_model == "gpt-5.5"
