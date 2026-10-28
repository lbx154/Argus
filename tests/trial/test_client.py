"""The trial-key client: setup, key prompt, provider environment and model selection."""
from __future__ import annotations

import json
import os

import pytest

from argus.trial import client, hosting


@pytest.mark.parametrize("url", ["http://example.com", "https://user:pass@example.com", "https://example.com/v1", "https://example.com?key=value", "file:///tmp/socket"])
def test_connect_rejects_insecure_or_ambiguous_url(url):
    with pytest.raises(ValueError, match="HTTPS origin"):
        client.connect(url)


def test_noninteractive_trial_never_prompts_for_a_key(monkeypatch):
    monkeypatch.delenv("ARGUS_TRIAL_KEY", raising=False)
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(client.getpass, "getpass", lambda *_: pytest.fail("Must not prompt"))
    with pytest.raises(ValueError, match="ARGUS_TRIAL_KEY"):
        client.connect("https://argusbot.cn", non_interactive=True)


def test_default_trial_command_uses_the_public_argus_site():
    from argus.apps.cli import build_parser

    args = build_parser().parse_args(["--setup", "--trial"])
    assert args.setup and args.trial_url == "https://argusbot.cn"


def test_trial_setup_selects_copilot_and_does_not_use_pi(monkeypatch):
    from argus.tools import setup

    captured = []
    monkeypatch.setattr(hosting, "setup", lambda url, **kwargs: captured.append(url) or 0)
    assert setup.run_setup(trial_url="https://trial.example.com") == 0
    assert captured == ["https://trial.example.com"]
    assert setup.run_setup(trial_url="https://trial.example.com", backend="pi") == 2
    assert setup.run_setup(trial_url="https://trial.example.com", backend="codex") == 2


def test_missing_copilot_is_installed_automatically(monkeypatch):
    from types import SimpleNamespace

    from argus.agent_cli import runner_backend

    installed = []
    monkeypatch.setattr(runner_backend, "resolve_runner_bin", lambda _backend: "/bin/copilot" if installed else None)
    monkeypatch.setattr(client.shutil, "which", lambda name: "/bin/npm")

    def run(argv, **kwargs):
        if argv[0] == "/bin/npm":
            installed.append(argv)
            return SimpleNamespace(returncode=0)
        assert argv == ["/bin/copilot", "help", "providers"]
        return SimpleNamespace(returncode=0, stdout="COPILOT_PROVIDER_WIRE_MODEL")

    monkeypatch.setattr(client.subprocess, "run", run)
    assert client.ensure_copilot() == "/bin/copilot"
    assert installed == [["/bin/npm", "install", "-g", "@github/copilot@latest"]]


def test_trial_workers_replace_inherited_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    key = "argus_trial_" + "a" * 64
    client.profile_path().write_text(json.dumps({"base_url": "https://trial.example.com/v1", "api_key": key}))
    env = client.apply_trial_provider({
        client.TRIAL_ENV: "1", "COPILOT_PROVIDER_BASE_URL": "https://old.example.com",
        "COPILOT_PROVIDER_BEARER_TOKEN": "old-secret", "GITHUB_TOKEN": "github-secret",
        "COPILOT_PROVIDER_HEADERS": "Authorization: old-secret",
    })
    assert env["COPILOT_PROVIDER_BASE_URL"] == "https://trial.example.com/v1"
    assert env["COPILOT_PROVIDER_API_KEY"] == key
    assert env["COPILOT_PROVIDER_WIRE_MODEL"] == "argus-trial"
    assert env["COPILOT_MODEL"] == env["COPILOT_PROVIDER_MODEL_ID"] == "argus-trial"
    assert env["COPILOT_HOME"] == str(tmp_path / "copilot-trial-home")
    assert "COPILOT_PROVIDER_BEARER_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert env["COPILOT_PROVIDER_HEADERS"] == "User-Agent: Argus/0.1.1"
    client.profile_path().write_text("invalid")
    with pytest.raises(ValueError):
        client.apply_trial_provider({client.TRIAL_ENV: "1"})


def test_failed_trial_verification_restores_previous_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus.core import backend_readiness

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv(client.TRIAL_ENV, "0")
    previous = b'{"base_url":"https://old.example.com/v1","api_key":"old-trial-key"}'
    client.profile_path().write_bytes(previous)
    monkeypatch.setattr(client, "ensure_copilot", lambda: "/bin/copilot")
    monkeypatch.setattr(client, "connect", lambda _url, **kwargs: ("https://trial.example.com/v1", "argus_trial_" + "a" * 64))
    monkeypatch.setattr(backend_readiness, "check_backend_readiness", lambda *a, **kw: SimpleNamespace(ok=False))
    monkeypatch.setattr(backend_readiness, "format_backend_readiness", lambda _report: "not ready")
    assert client.setup_trial("https://trial.example.com") == 1
    assert client.profile_path().read_bytes() == previous
    assert os.environ[client.TRIAL_ENV] == "0"


def test_explicit_incompatible_trial_model_is_rejected_without_rewriting_personal_mode(tmp_path, monkeypatch):
    from argus.core.knob_store import write_persisted_knobs
    from argus.core.knobs import resolve_role_model, resolve_role_reasoning_effort

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv(client.TRIAL_ENV, raising=False)
    write_persisted_knobs({client.TRIAL_ENV: "1", "ARGUS_SKILL_MODEL": "gpt-4.1"})
    assert resolve_role_model("engineer", env={}) == "gpt-4.1"
    assert resolve_role_reasoning_effort("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", env={}) == "high"
    with pytest.raises(ValueError, match="argus-trial / auto"):
        client.trial_model_options("gpt-4.1", "low")
    assert client.trial_model_options("auto", "low") == ("argus-trial", "low")
    assert resolve_role_model("engineer", env={}) == "gpt-4.1"
    monkeypatch.setenv(client.TRIAL_ENV, "0")
    assert client.trial_model_options("gpt-4.1", "low") == ("gpt-4.1", "low")
    assert resolve_role_model("engineer", env={client.TRIAL_ENV: "0"}) == "gpt-4.1"
