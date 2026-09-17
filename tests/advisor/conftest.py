import pytest


@pytest.fixture(autouse=True)
def isolated_advisor_state(tmp_path, monkeypatch):
    from argus.adapters.agent_cli_backend import _core
    from argus.core import secret_guard

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TRIAL", "0")
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "off")
    monkeypatch.setattr(secret_guard, "known_secret_values", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(_core, "known_secret_values", lambda *_args, **_kwargs: ())
