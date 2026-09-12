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
        assert "fixture-provider-credential" not in config_text
        assert "fixture-provider-credential" not in json.dumps(persisted)
    assert transcript.read_text() == '{"text":"existing conversation"}\n'


def test_admin_pi_does_not_fall_back_to_another_backend_when_binary_is_missing(tmp_path):
    with pytest.raises(ValueError, match="Argus-Pi executable"):
        configure_provider(tmp_path, tmp_path / "missing", Mock(spec=Vault))
