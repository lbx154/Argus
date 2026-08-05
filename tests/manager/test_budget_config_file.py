import os
from types import SimpleNamespace

from argus_skill.manager.config_intent import _apply_config_intent


def test_manager_budget_intent_writes_config_json(
    tmp_path, monkeypatch
) -> None:
    # The sole host-global cap is an ordinary config.json knob.
    from argus_skill.core.knob_store import read_persisted_knobs

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    intent = SimpleNamespace(
        knob="global_daily_cap",
        roles=(),
        value="42",
    )

    assert _apply_config_intent(mem, intent, {}, on_confirm=lambda _line: None)
    stored = read_persisted_knobs().get("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD")
    assert stored is not None and float(stored) == 42.0
    assert float(os.environ["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"]) == 42.0


def test_manager_rejects_free_text_model_without_poisoning_environment(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import read_persisted_knobs

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_MODEL", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    intent = SimpleNamespace(
        knob="model",
        roles=["engineer"],
        value="please use whatever model is best for this task",
    )
    confirmations: list[str] = []

    assert _apply_config_intent(mem, intent, {}, on_confirm=confirmations.append)

    assert "ARGUS_SKILL_ENGINEER_MODEL" not in os.environ
    assert "ARGUS_SKILL_ENGINEER_MODEL" not in read_persisted_knobs()
    assert confirmations and "not a model id" in confirmations[0]


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
