import json
import os
from unittest.mock import patch

import pytest

from argus_skill.core.knob_store import read_persisted_knobs, write_persisted_knobs
from argus_skill.trial.web_runtime import configure_provider


def test_pi_profile_selects_all_roles_and_own_provider(tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setattr("argus_skill.core.knob_store.write_persisted_knobs",
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
def test_restart_preserves_map_preference_without_lowering_research_roles(tmp_path, monkeypatch, saved_effort, environment_effort, expected):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.delenv("ARGUS_SKILL_MAP_REASONING_EFFORT", raising=False)
    if saved_effort is not None:
        write_persisted_knobs({"ARGUS_SKILL_MAP_REASONING_EFFORT": saved_effort})
    if environment_effort is not None:
        monkeypatch.setenv("ARGUS_SKILL_MAP_REASONING_EFFORT", environment_effort)
    with patch.dict(os.environ):
        configure_provider(tmp_path, {"api_key": "test-only-credential"})
        saved = read_persisted_knobs()
        assert os.environ["ARGUS_SKILL_MAP_REASONING_EFFORT"] == expected
        assert saved["ARGUS_SKILL_MAP_REASONING_EFFORT"] == expected
        assert all(saved[f"ARGUS_SKILL_{role}_REASONING_EFFORT"] == "high"
                   for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER"))
