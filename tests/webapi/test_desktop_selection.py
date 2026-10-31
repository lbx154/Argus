"""Native desktop choices are atomic; ordinary Web deployment semantics stay intact."""
import os

import pytest

from argus.core.desktop_selection import SELECTED_BACKEND, prepare_selection, profile_key
from argus.core.knob_store import read_persisted_knobs, write_persisted_knobs
from argus.core.knobs import resolve_role_model
from argus.webapi.mission_items import prefer_saved_over_deployment_env, set_operator_config


@pytest.mark.parametrize("backend", ["pi", "dsh", "copilot", "codex", "claude", "qoder"])
def test_native_selection_keeps_type_path_and_clears_foreign_model_context(backend):
    saved = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "old-model",
             "ARGUS_SKILL_MANAGER_MODEL": "old-manager", "ARGUS_SKILL_RUNNER_BIN": "old/copilot.cmd"}
    write_persisted_knobs(saved)
    env = {"ARGUS_DESKTOP_LAUNCH_NONCE": "fixture", "ARGUS_SKILL_RUNNER_BACKEND": backend,
           "ARGUS_SKILL_RUNNER_BIN": f"own/{backend}.cmd", "ARGUS_SKILL_MODEL": "ambient"}
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in prefer_saved_over_deployment_env(env)
    assert env["ARGUS_SKILL_RUNNER_BACKEND"] == env[SELECTED_BACKEND] == backend
    assert env["ARGUS_SKILL_RUNNER_BIN"] == f"own/{backend}.cmd"
    assert env["ARGUS_SKILL_MODEL"] == ("old-model" if backend == "copilot" else "auto")
    assert read_persisted_knobs() == saved


@pytest.mark.parametrize("raw,expected", [(" DSH ", "dsh"), ("opencod", "opencode")])
def test_native_selection_reuses_core_backend_validation(raw, expected):
    env = {"ARGUS_DESKTOP_LAUNCH_NONCE": "fixture", "ARGUS_SKILL_RUNNER_BACKEND": raw}
    assert prepare_selection(env, {}) == expected
    assert env[SELECTED_BACKEND] == expected


def test_unknown_native_backend_does_not_partially_mutate_selection():
    env = {"ARGUS_DESKTOP_LAUNCH_NONCE": "fixture", "ARGUS_SKILL_RUNNER_BACKEND": "copilto"}
    before = dict(env)
    with pytest.raises(ValueError):
        prepare_selection(env, {})
    assert env == before


def test_retired_trial_pins_are_not_reused_for_a_personally_installed_copilot():
    saved = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_COPILOT_TRIAL": "1",
             "ARGUS_SKILL_MODEL": "argus-trial", "ARGUS_SKILL_MANAGER_MODEL": "argus-trial"}
    env = {"ARGUS_DESKTOP_LAUNCH_NONCE": "fixture", "ARGUS_SKILL_RUNNER_BACKEND": "copilot"}
    prepare_selection(env, saved)
    assert env["ARGUS_SKILL_MODEL"] == "auto"
    assert env["ARGUS_SKILL_MANAGER_MODEL"] == "inherit"
    assert env["ARGUS_SKILL_COPILOT_TRIAL"] == "0"
    assert saved["ARGUS_SKILL_COPILOT_TRIAL"] == "1"  # Web trial state is untouched.


def test_web_deployment_still_prefers_saved_choice():
    write_persisted_knobs({"ARGUS_SKILL_RUNNER_BACKEND": "claude", "ARGUS_SKILL_MODEL": "saved"})
    env = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "deployment"}
    assert set(prefer_saved_over_deployment_env(env)) == {"ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_MODEL"}
    assert SELECTED_BACKEND not in env


def test_pi_dsh_pi_preserves_separate_model_choices_and_inheritance(monkeypatch):
    original = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "old-model"}
    write_persisted_knobs(original)
    monkeypatch.setenv("ARGUS_DESKTOP_LAUNCH_NONCE", "fixture")
    for backend, choice in [("pi", "provider/model-a"), ("dsh", "deepseek-official/deepseek-flash")]:
        os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = backend
        prepare_selection(os.environ, read_persisted_knobs())
        assert resolve_role_model("manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=backend) == ""
        set_operator_config("model", choice, apply_to_roles=True)
        assert resolve_role_model("manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=backend) == choice
    os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = "pi"
    prepare_selection(os.environ, read_persisted_knobs())
    assert resolve_role_model("manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend="pi") == "provider/model-a"
    saved = read_persisted_knobs()
    assert all(saved[k] == v for k, v in original.items())
    assert saved[profile_key("dsh", "ARGUS_SKILL_MODEL")] == "deepseek-official/deepseek-flash"
    before = dict(saved)
    with pytest.raises(ValueError, match="桌面设置"):
        set_operator_config("backend", "dsh")
    assert read_persisted_knobs() == before


def test_chat_settings_cannot_change_only_half_of_a_desktop_selection(monkeypatch):
    from types import SimpleNamespace

    from argus.manager import config_intent
    from argus.manager.form import ConfigIntent

    original = {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "old-model"}
    write_persisted_knobs(original)
    monkeypatch.setenv(SELECTED_BACKEND, "pi")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "pi")
    monkeypatch.setattr(config_intent, "_invalidate_manager_runner", lambda *a, **k: None)
    lines = []
    config_intent._apply_config_intent(SimpleNamespace(), [ConfigIntent("backend", (), "dsh")], {}, on_confirm=lines.append)
    assert "桌面设置" in lines[-1]
    assert read_persisted_knobs() == original
    config_intent._apply_config_intent(SimpleNamespace(), [ConfigIntent("model", (), "auto")], {}, on_confirm=lines.append)
    saved = read_persisted_knobs()
    assert all(saved[k] == value for k, value in original.items())
    assert saved[profile_key("pi", "ARGUS_SKILL_MODEL")] == "auto"


def test_legacy_matching_model_pins_are_retained_and_foreign_trial_is_disabled():
    saved = {"ARGUS_SKILL_RUNNER_BACKEND": "pi", "ARGUS_SKILL_MODEL": "custom/model",
             "ARGUS_SKILL_MANAGER_MODEL": "custom/manager"}
    env = {"ARGUS_DESKTOP_LAUNCH_NONCE": "fixture", "ARGUS_SKILL_RUNNER_BACKEND": "pi",
           "ARGUS_SKILL_COPILOT_TRIAL": "1", "ARGUS_SKILL_BACKEND_AUTH_MODE": "api_key"}
    prepare_selection(env, saved)
    assert env["ARGUS_SKILL_MODEL"] == "custom/model"
    assert env["ARGUS_SKILL_MANAGER_MODEL"] == "custom/manager"
    assert env["ARGUS_SKILL_COPILOT_TRIAL"] == "0"
    assert env["ARGUS_SKILL_BACKEND_AUTH_MODE"] == "subscription_cli"
    once = dict(env)
    prepare_selection(env, saved)
    assert env == once
