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


def test_manager_applies_backend_and_model_batch_without_codex_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import read_persisted_knobs
    from argus_skill.life.router import ConfigIntent

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    intents = (
        ConfigIntent(knob="backend", roles=(), value="pi"),
        ConfigIntent(knob="model", roles=(), value="gpt5.6sol"),
    )
    confirmations: list[str] = []
    chat_state = {"backend": "copilot", "last_thread_id": "stale"}

    assert _apply_config_intent(
        mem,
        intents,
        chat_state,
        on_confirm=confirmations.append,
    )

    stored = read_persisted_knobs()
    assert stored["ARGUS_SKILL_RUNNER_BACKEND"] == "pi"
    assert stored["ARGUS_SKILL_MODEL"] == "gpt-5.6-sol"
    assert {
        stored["ARGUS_SKILL_MANAGER_BACKEND"],
        stored["ARGUS_SKILL_PLANNER_BACKEND"],
        stored["ARGUS_SKILL_ENGINEER_BACKEND"],
        stored["ARGUS_SKILL_REVIEWER_BACKEND"],
    } == {"pi"}
    assert {
        stored["ARGUS_SKILL_MANAGER_MODEL"],
        stored["ARGUS_SKILL_PLAN_MODEL"],
        stored["ARGUS_SKILL_ENGINEER_MODEL"],
        stored["ARGUS_SKILL_REVIEWER_MODEL"],
    } == {"gpt-5.6-sol"}
    assert os.environ["ARGUS_SKILL_RUNNER_BACKEND"] == "pi"
    assert os.environ["ARGUS_SKILL_MODEL"] == "gpt-5.6-sol"
    assert confirmations == [
        "Set all Argus roles CLI backend to pi.",
        "Set all Argus roles model to gpt-5.6-sol.",
    ]
    assert chat_state["backend"] == "pi"
    assert chat_state["last_thread_id"] is None


def test_manager_rejects_invalid_batch_atomically(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import read_persisted_knobs
    from argus_skill.life.router import ConfigIntent

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    confirmations: list[str] = []

    assert _apply_config_intent(
        mem,
        (
            ConfigIntent(knob="backend", roles=(), value="pi"),
            ConfigIntent(knob="model", roles=(), value="not a model id"),
        ),
        {},
        on_confirm=confirmations.append,
    )

    assert read_persisted_knobs() == {}
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ
    assert "ARGUS_SKILL_MODEL" not in os.environ
    assert confirmations and "nothing changed" in confirmations[0]


def test_manager_rejects_unknown_backend_without_codex_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import read_persisted_knobs
    from argus_skill.life.router import ConfigIntent

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    mem = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    confirmations: list[str] = []

    assert _apply_config_intent(
        mem,
        ConfigIntent(knob="backend", roles=(), value="pi; SET model ALL x"),
        {},
        on_confirm=confirmations.append,
    )

    assert read_persisted_knobs() == {}
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ
    assert confirmations and "nothing changed" in confirmations[0]


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
