import json
import os
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet

from argus_skill.core.knob_store import read_persisted_knobs, write_persisted_knobs
from argus_skill.trial.admin_runtime import configure_provider
from argus_skill.trial.secrets import Vault


def test_admin_pi_configuration_preserves_state_and_never_embeds_provider_secret(tmp_path, monkeypatch):
    root = tmp_path / "state"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    write_persisted_knobs({
        "ARGUS_SKILL_MANAGER_BACKEND": "copilot",
        "ARGUS_SKILL_MANAGER_RUNNER_BIN": "/obsolete/copilot",
        "ARGUS_SKILL_MANAGER_MODEL": "old-model",
        "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "0",
    })
    project = root / "projects" / "existing"
    project.mkdir(parents=True)
    transcript = project / "transcript.jsonl"
    transcript.write_text('{"text":"existing conversation"}\n')
    binary = tmp_path / "argus-pi"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    key = tmp_path / "master.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    vault = Vault(key, tmp_path / "credential.enc")
    vault.save("fixture-provider-credential")

    with patch.dict(os.environ):
        configure_provider(root, binary, vault)
        assert os.environ["ARGUS_ADMIN_PROVIDER_TOKEN"] == "fixture-provider-credential"
        persisted = read_persisted_knobs()
        for role in ("MANAGER", "ENGINEER", "REVIEWER", "PLANNER", "SUPERVISOR", "CURATOR"):
            assert persisted[f"ARGUS_SKILL_{role}_BACKEND"] == "pi"
            assert persisted[f"ARGUS_SKILL_{role}_RUNNER_BIN"] == str(binary)
        assert persisted["ARGUS_SKILL_MANAGER_MODEL"] == "gpt-5.5"
        assert persisted["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"] == "0"
        config_text = (root / "argus-pi-admin" / "models.json").read_text()
        provider = json.loads(config_text)["providers"]["argus"]
        assert provider["api"] == "openai-responses"
        assert provider["apiKey"] == "$ARGUS_ADMIN_PROVIDER_TOKEN"
        assert provider["models"][0]["compat"]["supportsStrictMode"] is True
        assert "fixture-provider-credential" not in config_text
        assert "fixture-provider-credential" not in json.dumps(persisted)
    assert transcript.read_text() == '{"text":"existing conversation"}\n'


def test_admin_pi_does_not_fall_back_to_another_backend_when_binary_is_missing(tmp_path):
    with pytest.raises(ValueError, match="Argus-Pi executable"):
        configure_provider(tmp_path, tmp_path / "missing", Mock(spec=Vault))


def test_existing_workspace_enrolls_with_trial_meter_and_live_capture_without_moving_projects(tmp_path, monkeypatch):
    root = tmp_path / "original-state"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    project = root / "projects" / "s-existing"
    project.mkdir(parents=True)
    transcript = project / "transcript.jsonl"
    transcript.write_text('{"text":"original project history"}\n')
    binary = tmp_path / "pi"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    vault = Mock(spec=Vault)
    vault.credential.return_value = "argus_trial_" + "a" * 64
    socket = tmp_path / "run" / "training.sock"
    with patch.dict(os.environ, {"ARGUS_ADMIN_PROVIDER_TOKEN": "old-provider-secret"}):
        configure_provider(root, binary, vault, tenant="trial-11", training_socket=socket)
        vault.read.assert_not_called()
        vault.credential.assert_called_once_with("trial-11")
        provider = json.loads((root / "argus-pi" / "models.json").read_text())["providers"]["argus"]
        assert provider["api"] == "openai-completions"
        assert provider["apiKey"] == vault.credential.return_value
        assert provider["baseUrl"] == "http://127.0.0.1:18765/v1"
        assert "headers" not in provider and "old-provider-secret" not in json.dumps(provider)
        assert "ARGUS_ADMIN_PROVIDER_TOKEN" not in os.environ
        assert os.environ["ARGUS_TRAINING_BRIDGE_SOCKET"] == str(socket)
        assert os.environ["ARGUS_TRIAL_HARNESS"] == "argus-pi"
        assert os.environ["ARGUS_SKILL_HOME"] == str(root)
        assert os.environ["ARGUS_SKILL_PI_SESSION_DIR"] == str(root / "pi-sessions")
        assert os.environ["ARGUS_SKILL_MAP_REASONING_EFFORT"] == "medium"
    assert transcript.read_text() == '{"text":"original project history"}\n'


def test_native_restart_keeps_explicit_map_effort_and_research_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_MAP_REASONING_EFFORT", raising=False)
    write_persisted_knobs({"ARGUS_SKILL_MAP_REASONING_EFFORT": "low",
                          "ARGUS_SKILL_ENGINEER_REASONING_EFFORT": "high"})
    binary = tmp_path / "pi"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    vault = Mock(spec=Vault)
    vault.credential.return_value = "test-only-credential"
    with patch.dict(os.environ):
        configure_provider(tmp_path, binary, vault, tenant="trial-11", training_socket=tmp_path / "training.sock")
        assert os.environ["ARGUS_SKILL_MAP_REASONING_EFFORT"] == "low"
        saved = read_persisted_knobs()
        assert saved["ARGUS_SKILL_MAP_REASONING_EFFORT"] == "low"
        assert saved["ARGUS_SKILL_ENGINEER_REASONING_EFFORT"] == "high"
