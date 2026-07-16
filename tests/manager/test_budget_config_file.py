import os
from types import SimpleNamespace

from argus_skill.core.project_budget import read_project_budget
from argus_skill.manager.config_intent import _apply_config_intent


def test_manager_budget_intent_updates_project_file_not_environment(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PER_MISSION_CAP_USD", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    intent = SimpleNamespace(
        knob="per_mission_cap",
        roles=(),
        value="42",
    )

    assert _apply_config_intent(mem, intent, {}, on_confirm=lambda _line: None)
    assert read_project_budget(tmp_path).per_mission_cap_usd == 42
    assert "ARGUS_SKILL_PER_MISSION_CAP_USD" not in os.environ


def test_manager_config_failure_does_not_change_environment(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core import knob_store

    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    intent = SimpleNamespace(knob="model", roles=["engineer"], value="new-model")
    confirmations: list[str] = []
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_MODEL", raising=False)
    monkeypatch.setattr(knob_store, "write_persisted_knobs", lambda _values: False)

    assert _apply_config_intent(
        mem,
        intent,
        {},
        on_confirm=confirmations.append,
    )

    assert "ARGUS_SKILL_ENGINEER_MODEL" not in os.environ
    assert confirmations == ["Could not persist configuration; nothing changed."]
