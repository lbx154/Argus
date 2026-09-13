"""Local preview trial integration, without credentials or paid model calls."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from argus_skill.trial import client, desktop
from argus_skill.trial.storage import write_private

KEY = "argus_trial_" + "a" * 64
OLD_KEY = "argus_trial_" + "b" * 64


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for name in list(os.environ):
        if name.startswith("ARGUS_SKILL_") and name != "ARGUS_SKILL_HOME":
            monkeypatch.delenv(name)
    monkeypatch.delenv("ARGUS_DESKTOP_TRIAL_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)
    return tmp_path


def test_prepare_never_changes_active_profile_or_model_knobs(home, monkeypatch):
    from argus_skill.core.knob_store import read_persisted_knobs, write_persisted_knobs
    from argus_skill.core import backend_readiness
    from argus_skill.tools import setup
    from argus_skill.trial import native_cli

    old = json.dumps({"base_url": "https://argusbot.cn/v1", "api_key": OLD_KEY}).encode()
    write_private(home / "copilot-trial.json", old)
    assert write_persisted_knobs({"ARGUS_SKILL_MODEL": "own-model", "ARGUS_SKILL_RUNNER_BACKEND": "pi"})
    before = read_persisted_knobs()
    monkeypatch.setattr(desktop, "query_status", lambda *_: {"tokens_remaining": 42, "token_limit": 100})
    monkeypatch.setattr(native_cli, "install_native_copilot", lambda: "isolated-copilot.exe")
    monkeypatch.setattr(backend_readiness, "check_backend_readiness", lambda *a, **k: SimpleNamespace(ok=True))
    def smoke(*args, **kwargs):
        assert json.loads(client.profile_path().read_text())["api_key"] == KEY
        assert (home / "copilot-trial.json").read_bytes() == old
        assert read_persisted_knobs() == before
        return True
    monkeypatch.setattr(setup, "_verify_setup_smoke", smoke)
    executable, balance = desktop.prepare(KEY, progress=lambda _: None)
    assert executable == "isolated-copilot.exe" and balance["tokens_remaining"] == 42
    assert (home / "copilot-trial.json").read_bytes() == old
    assert read_persisted_knobs() == before
    assert "ARGUS_DESKTOP_TRIAL_PROFILE" not in os.environ
    assert "ARGUS_SKILL_COPILOT_TRIAL" not in os.environ


def test_failed_prepare_leaves_personal_configuration_intact(home, monkeypatch):
    from argus_skill.core import backend_readiness
    from argus_skill.tools import setup
    from argus_skill.trial import native_cli

    monkeypatch.setattr(desktop, "query_status", lambda *_: {"tokens_remaining": 1, "token_limit": 100})
    monkeypatch.setattr(native_cli, "install_native_copilot", lambda: "copilot.exe")
    monkeypatch.setattr(backend_readiness, "check_backend_readiness", lambda *a, **k: SimpleNamespace(ok=True))
    monkeypatch.setattr(setup, "_verify_setup_smoke", lambda *a, **k: False)
    with pytest.raises(ValueError, match="原设置未更改"):
        desktop.prepare(KEY, progress=lambda _: None)
    assert not (home / "copilot-trial.json").exists()
    assert "ARGUS_DESKTOP_TRIAL_PROFILE" not in os.environ


def test_incompatible_role_is_rejected_without_overwriting(home, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_BACKEND", "pi")
    with pytest.raises(ValueError, match="manager"):
        client.validate_role_overrides()
    assert os.environ["ARGUS_SKILL_MANAGER_BACKEND"] == "pi"


def test_personal_mode_does_not_load_trial_profile(home):
    values = {client.TRIAL_ENV: "0", "GITHUB_TOKEN": "dummy-own-account"}
    assert client.apply_trial_provider(values) == values
    assert not (home / "copilot-trial.json").exists()


def test_trial_binding_uses_separate_home_and_clears_personal_auth(home):
    write_private(home / "copilot-trial.json", json.dumps({
        "base_url": "https://argusbot.cn/v1", "api_key": KEY,
    }).encode())
    result = client.apply_trial_provider({client.TRIAL_ENV: "1", "GH_TOKEN": "dummy-own-account"})
    assert "GH_TOKEN" not in result
    assert result["COPILOT_PROVIDER_API_KEY"] == KEY
    assert result["COPILOT_PROVIDER_WIRE_MODEL"] == "argus-trial"
    assert result["COPILOT_HOME"] == str(home / "copilot-trial-home")


def test_status_cache_never_contains_key_and_flags_offline(home, monkeypatch):
    write_private(home / "copilot-trial.json", json.dumps({
        "base_url": "https://argusbot.cn/v1", "api_key": KEY,
    }).encode())
    monkeypatch.setattr(desktop, "query_status", lambda *_: {"tokens_remaining": 45, "token_limit": 100})
    assert desktop.current_status()["stale"] is False
    assert KEY not in (home / "trial-status.json").read_text()
    def offline(*args):
        raise ValueError("untrusted upstream detail " + KEY)
    monkeypatch.setattr(desktop, "query_status", offline)
    result = desktop.current_status()
    assert result["stale"] is True and result["tokens_remaining"] == 45
    assert KEY not in json.dumps(result)


def test_invalid_key_never_contacts_network(home, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid keys must be rejected before a network call")
    monkeypatch.setattr(client.urllib.request, "build_opener", unexpected)
    with pytest.raises(ValueError, match="内部测试 Key"):
        client.query_status("https://argusbot.cn", "not-a-key")


def test_trial_usage_keeps_counts_without_personal_dollar_cost(home):
    from argus_skill.core.usage import build_usage_record
    from argus_skill.core.token_usage import TokenUsage
    record = build_usage_record(call_id="preview-test", project_root=home, mission_id=None,
        provider="copilot", model="argus-trial", run_label="test", started_at=1,
        completed_at=2, status="completed", hosted_trial=True,
        copilot_token_billing_expected=True,
        token_usage=TokenUsage(input_tokens=120, output_tokens=30,
                               input_tokens_present=True, output_tokens_present=True))
    assert record.cost_usd == 0 and record.pricing_tier == "hosted_trial"
    assert record.input_tokens == 120 and record.output_tokens == 30
