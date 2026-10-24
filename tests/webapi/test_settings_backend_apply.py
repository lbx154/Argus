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


def test_an_app_built_directly_reports_the_saved_backend_as_saved(tmp_path, monkeypatch) -> None:
    # Deployments may build the app and run their own server instead of
    # calling ``serve``; the saved choice must still win over their env.
    import pytest

    pytest.importorskip("fastapi")
    from argus.core.config_snapshot import build_config_snapshot
    from argus.webapi.server import create_app

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    (tmp_path / "config.json").write_text(
        json.dumps({"ARGUS_SKILL_RUNNER_BACKEND": "copilot"}), encoding="utf-8"
    )

    create_app(global_root=tmp_path, auth_token="t")

    knob = next(
        row for row in build_config_snapshot()["operator_knobs"]
        if row["name"] == "ARGUS_SKILL_RUNNER_BACKEND"
    )
    assert (knob["value"], knob["source"]) == ("copilot", "persisted")
