import json
import os
from unittest.mock import patch

import pytest

from argus.core.knob_store import read_persisted_knobs, write_persisted_knobs
from argus_trial_server import DEFAULT_UPSTREAM_MODEL
from argus_trial_server.web_runtime import configure_provider


def test_pi_registers_the_same_explicit_models_used_by_the_trial_gateway(tmp_path, monkeypatch):
    from argus_trial_server.model_catalog import configured_model_ids

    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv("ARGUS_TRIAL_MODELS", "expert-model,fast-model,expert-model")
    with patch.dict(os.environ):
        configure_provider(tmp_path, {"api_key": "test-key"})
    provider = json.loads((tmp_path / "argus-pi/models.json").read_text())["providers"]["argus"]
    assert tuple(row["id"] for row in provider["models"]) == configured_model_ids()
    assert tuple(row["id"] for row in provider["models"]) == (DEFAULT_UPSTREAM_MODEL, "expert-model", "fast-model")


@pytest.mark.parametrize(("saved_effort", "environment_effort", "expected"), [
    (None, None, "low"), ("low", None, "low"), ("high", None, "high"), ("high", "low", "low"),
])
def test_restart_preserves_light_effort_without_lowering_research_roles(tmp_path, monkeypatch, saved_effort, environment_effort, expected):
    knob = "ARGUS_SKILL_LIGHT_REASONING_EFFORT"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.delenv(knob, raising=False)
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
