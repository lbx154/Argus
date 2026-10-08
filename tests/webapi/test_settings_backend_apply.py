"""Settings Apply: re-submitting the backend already in effect writes nothing."""
from __future__ import annotations

import json

from argus.webapi.mission_items import set_operator_config


def _stored(tmp_path) -> dict:
    path = tmp_path / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_model_change_with_env_backend_does_not_persist_a_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")

    set_operator_config("model", "some-model")
    result = set_operator_config("backend", "copilot")

    assert result["unchanged"] is True
    assert result["source"] == "env"
    stored = _stored(tmp_path)
    assert stored.get("ARGUS_SKILL_MODEL") == "some-model"
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in stored


def test_backend_equal_to_default_is_not_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    from argus.core.knobs import KNOBS

    default = next(k.default for k in KNOBS if k.name == "ARGUS_SKILL_RUNNER_BACKEND")
    assert set_operator_config("backend", default)["unchanged"] is True
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in _stored(tmp_path)


def test_a_real_backend_change_is_still_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")

    result = set_operator_config("backend", "codex")

    assert not result.get("unchanged")
    assert _stored(tmp_path)["ARGUS_SKILL_RUNNER_BACKEND"] == "codex"
