import json
import os
from unittest.mock import patch

import pytest

from argus.core.knob_store import read_persisted_knobs, write_persisted_knobs
from argus.trial import DEFAULT_UPSTREAM_MODEL
from argus.trial.web_runtime import configure_provider


def test_pi_registers_the_same_explicit_models_used_by_the_trial_gateway(tmp_path, monkeypatch):
    from argus.trial.model_catalog import configured_model_ids

    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv("ARGUS_TRIAL_MODELS", "expert-model,fast-model,expert-model")
    with patch.dict(os.environ):
        configure_provider(tmp_path, {"api_key": "test-key"})
    provider = json.loads((tmp_path / "argus-pi/models.json").read_text())["providers"]["argus"]
    assert tuple(row["id"] for row in provider["models"]) == configured_model_ids()
    assert tuple(row["id"] for row in provider["models"]) == (DEFAULT_UPSTREAM_MODEL, "expert-model", "fast-model")


def test_pi_profile_selects_all_roles_and_own_provider(tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setattr("argus.core.knob_store.write_persisted_knobs",
                        lambda values: saved.update(values) or True)
    configure_provider(tmp_path, {"api_key": "test-only-credential"})
    assert saved["ARGUS_SKILL_RUNNER_BACKEND"] == "pi"
    assert saved["ARGUS_SKILL_COPILOT_TRIAL"] == "0"
    assert saved["ARGUS_SKILL_RUNNER_BIN"] == "/usr/local/bin/argus-pi"
    assert saved["ARGUS_SKILL_BACKEND_AUTH_MODE"] == "subscription_cli"
    assert all(saved[f"ARGUS_SKILL_{role}_BACKEND"] == "pi"
               for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER", "CURATOR", "SUPERVISOR"))
    path = tmp_path / "argus-pi/models.json"
    provider = json.loads(path.read_text())["providers"]["argus"]
    assert provider["apiKey"] == "test-only-credential"
    assert provider["baseUrl"] == "http://127.0.0.1:18765/v1"
    assert provider["models"] == [{"id": "gpt-5.5", "reasoning": True}]
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(("saved_effort", "environment_effort", "expected"), [
    (None, None, "medium"), ("low", None, "low"), ("high", None, "high"),
    ("auto", None, "auto"), ("high", "low", "low"),
])
@pytest.mark.parametrize("knob,default", [("ARGUS_SKILL_MAP_REASONING_EFFORT", "medium"),
                                         ("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "high")])
def test_restart_preserves_map_preference_without_lowering_research_roles(tmp_path, monkeypatch, saved_effort, environment_effort, expected, knob, default):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.delenv(knob, raising=False)
    if saved_effort is None and environment_effort is None:
        expected = default
    if saved_effort is not None:
        write_persisted_knobs({knob: saved_effort})
    if environment_effort is not None:
        monkeypatch.setenv(knob, environment_effort)
    with patch.dict(os.environ):
        configure_provider(tmp_path, {"api_key": "test-only-credential"})
        saved = read_persisted_knobs()
        assert os.environ[knob] == expected
        assert saved[knob] == expected
        assert all(saved[f"ARGUS_SKILL_{role}_REASONING_EFFORT"] == "high"
                   for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER"))
