from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet

from argus_skill.trial import client
from argus_skill.trial.gateway import Settings, create_app
from argus_skill.trial.secrets import Vault, write_private


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
    from argus_skill.apps.cli import build_parser

    args = build_parser().parse_args(["--setup", "--trial"])
    assert args.setup and args.trial_url == "https://argusbot.cn"


def test_trial_setup_selects_copilot_and_does_not_use_pi(monkeypatch):
    from argus_skill.tools import setup

    captured = []
    monkeypatch.setattr(client, "setup_trial", lambda url, **kwargs: captured.append(url) or 0)
    assert setup.run_setup(trial_url="https://trial.example.com") == 0
    assert captured == ["https://trial.example.com"]
    assert setup.run_setup(trial_url="https://trial.example.com", backend="pi") == 2
    assert setup.run_setup(trial_url="https://trial.example.com", backend="codex") == 2


def test_missing_copilot_is_installed_automatically(monkeypatch):
    from types import SimpleNamespace

    from argus_skill.agent_cli import runner_backend

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
    assert env["COPILOT_MODEL"] == env["COPILOT_PROVIDER_MODEL_ID"] == "gpt-5.5"
    assert env["COPILOT_HOME"] == str(tmp_path / "copilot-trial-home")
    assert "COPILOT_PROVIDER_BEARER_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert env["COPILOT_PROVIDER_HEADERS"] == "User-Agent: Argus/0.1.1"
    client.profile_path().write_text("invalid")
    with pytest.raises(ValueError):
        client.apply_trial_provider({client.TRIAL_ENV: "1"})


def test_failed_trial_verification_restores_previous_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus_skill.core import backend_readiness

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


def test_old_trial_models_resolve_to_the_current_provider_without_changing_personal_mode(tmp_path, monkeypatch):
    from argus_skill.core.knob_store import write_persisted_knobs
    from argus_skill.core.knobs import resolve_role_model, resolve_role_reasoning_effort

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv(client.TRIAL_ENV, raising=False)
    write_persisted_knobs({client.TRIAL_ENV: "1", "ARGUS_SKILL_MODEL": "gpt-4.1"})
    assert resolve_role_model("engineer", env={}) == "gpt-5.5"
    assert resolve_role_reasoning_effort("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", env={}) == "high"
    assert client.trial_model_options("gpt-4.1", "low") == ("gpt-5.5", "high")
    monkeypatch.setenv(client.TRIAL_ENV, "0")
    assert client.trial_model_options("gpt-4.1", "low") == ("gpt-4.1", "low")
    assert resolve_role_model("engineer", env={client.TRIAL_ENV: "0"}) == "gpt-4.1"


@pytest.mark.e2e
@pytest.mark.skipif(shutil.which("copilot") is None, reason="Copilot CLI required for real client smoke test")
@pytest.mark.parametrize("local_tool", ["view", "apply_patch"])
@pytest.mark.parametrize("transport_mode", ["oneshot", "acp"])
def test_real_argus_setup_and_copilot_tool_round_trip(tmp_path, monkeypatch, local_tool, transport_mode):
    """Real Argus -> real Copilot CLI -> HTTP gateway -> simulated upstream."""
    key = tmp_path / "key"
    state = tmp_path / "server"
    state.mkdir()
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("fake-github-secret")
    requests = []
    rejected = []
    from pydantic import ValidationError

    from argus_skill.trial import gateway

    original_prepare = gateway.prepare

    def inspect_payload(data, model):
        try:
            gateway.Completion.model_validate(data)
        except ValidationError as exc:
            rejected.extend(exc.errors(include_input=False))
            rejected.append({"snippy": data.get("snippy")})
        return original_prepare(data, model)

    monkeypatch.setattr(gateway, "prepare", inspect_payload)

    def upstream(request):
        assert str(request.url) == "https://api.githubcopilot.com/responses"
        assert request.headers["authorization"] == "Bearer fake-github-secret"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "gpt-5.5" and payload["reasoning"] == {"effort": "high"}
        messages = payload["input"]
        if any("TRIAL_REJECT_REQUEST" in str(m.get("content")) for m in messages):
            return httpx.Response(400, json={"error": {"message": "Invalid request fixture"}})
        if any("TRIAL_FOLLOW_UP" in str(m.get("content")) for m in messages):
            assert any("TRIAL_TOOL_OK" in str(m.get("content")) for m in messages)
            delta, finish = {"role": "assistant", "content": "TRIAL_FOLLOW_UP_OK"}, "stop"
        elif any("ARGUS_SETUP_OK" in str(m.get("content")) for m in messages):
            delta, finish = {"role": "assistant", "content": "ARGUS_SETUP_OK"}, "stop"
        elif any(m.get("type") in {"function_call_output", "custom_tool_call_output"} for m in messages):
            if local_tool == "view":
                assert any("trial-local-file-evidence" in str(m.get("output")) for m in messages if m.get("type") == "function_call_output")
            else:
                assert any(m.get("type") == "custom_tool_call_output" for m in messages)
                assert (tmp_path / "result.txt").read_text() == "trial-local-patch-evidence\n"
            delta, finish = {"role": "assistant", "content": "TRIAL_TOOL_OK"}, "stop"
        elif local_tool == "apply_patch":
            tool = next(t for t in payload["tools"] if t["name"] == "apply_patch")
            assert tool["type"] == "custom" and tool["format"]["type"] == "grammar"
            assert tool["format"]["syntax"] == "lark" and tool["format"]["definition"]
            assert "grammar" not in tool["format"]
            delta = {"role": "assistant", "tool_calls": [{
                "id": "call_patch", "type": "custom", "custom": {
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Add File: result.txt\n+trial-local-patch-evidence\n*** End Patch",
                },
            }]}
            finish = "tool_calls"
        else:
            assert any(t["name"] == "view" for t in payload["tools"])
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "call_read", "type": "function",
                "function": {"name": "view", "arguments": json.dumps({"path": str(tmp_path / "evidence.txt")})},
            }]}
            finish = "tool_calls"
        if finish == "tool_calls":
            call = delta["tool_calls"][0]
            kind = "custom_tool_call" if call["type"] == "custom" else "function_call"
            output = [{"type": kind, "call_id": call["id"], **call[call["type"]]}]
        else:
            output = [{"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": delta["content"]}]}]
        data = {"id": "test-response", "created_at": 1, "status": "completed", "output": output,
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
        content = "data: " + json.dumps({"type": "response.completed", "response": data}) + "\n\n"
        return httpx.Response(200, text=content, headers={"Content-Type": "text/event-stream"})

    app = create_app(Settings(state, key), transport=httpx.MockTransport(upstream))
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    credential = app.state.vault.credential("test-key")
    app.state.store.issue("test-key", credential)
    project = Path(__file__).resolve().parents[2]
    # Isolate Copilot/Argus state and remove all inherited provider credentials and
    # role overrides. Preserve HOME itself rather than repurposing it.
    env = {k: v for k, v in os.environ.items() if not (
        k.startswith(("ARGUS_", "PI_", "COPILOT_", "GH_", "GITHUB_")) or "TOKEN" in k or "API_KEY" in k
    )}
    env.update(
        ARGUS_SKILL_HOME=str(tmp_path / "argus"),
        PYTHONPATH=str(project),
        CI="true",
        ARGUS_TRIAL_KEY=credential,
    )
    (tmp_path / "evidence.txt").write_text("trial-local-file-evidence")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "argus_skill", "--setup", "--trial-url", f"http://127.0.0.1:{port}"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr + str(rejected)
        config = json.loads((tmp_path / "argus/copilot-trial.json").read_text())
        assert config["api_key"].startswith("argus_trial_")
        assert "fake-github-secret" not in json.dumps(config)
        assert "fake-access-secret" not in json.dumps(config)
        env.pop("ARGUS_TRIAL_KEY")
        # A fresh Python process proves persisted trial routing; this goes
        # through Argus's actual worker launch, not a manually configured CLI.
        label = "simple-1" if transport_mode == "acp" else "trial-tool-smoke"
        probe = (
            "from argus_skill.core.agent_probe import run_read_only_agent_prompt; "
            "r=run_read_only_agent_prompt(backend='copilot', executable=shutil.which('copilot'), "
            f"model='gpt-4.1', run_label={label!r}, prompt='Read "
            + str(tmp_path / "evidence.txt") + " and report TRIAL_TOOL_OK.'); "
            if local_tool == "view" else
            "from argus_skill.core.agent_probe import run_agent_repair_prompt; "
            "r=run_agent_repair_prompt(backend='copilot', executable=shutil.which('copilot'), "
            f"working_dir={str(tmp_path)!r}, model='gpt-4.1', run_label={label!r}, "
            "prompt='Create result.txt with trial-local-patch-evidence using apply_patch, then report TRIAL_TOOL_OK.'); "
        )
        if transport_mode == "acp":
            prompt = (
                f"Read {tmp_path / 'evidence.txt'} and report TRIAL_TOOL_OK."
                if local_tool == "view" else
                "Create result.txt with trial-local-patch-evidence using apply_patch, then report TRIAL_TOOL_OK."
            )
            probe = (
                "from argus_skill.agent_cli.copilot_acp import CopilotAcpClient; "
                "from argus_skill.agent_cli.agent_cli_runner import RunnerOptions; "
                "c=CopilotAcpClient(shutil.which('copilot'),model='gpt-5.5',reasoning_effort='high'); "
                f"o=RunnerOptions(working_dir={str(tmp_path)!r}); "
                f"r=c.run_prompt(prompt={prompt!r},resume_thread_id=None,options=o,run_label='simple-1'); "
                "assert r.turn_completed and 'TRIAL_TOOL_OK' in r.agent_messages[-1], r; "
                "r=c.run_prompt(prompt='TRIAL_FOLLOW_UP: what was your previous answer?',resume_thread_id=r.thread_id,options=o,run_label='simple-1'); "
                "assert r.turn_completed and 'TRIAL_FOLLOW_UP_OK' in r.agent_messages[-1], r; "
                "r=c.run_prompt(prompt='TRIAL_REJECT_REQUEST',resume_thread_id=r.thread_id,options=o,run_label='simple-1'); "
                "c.close(); "
                "assert r.turn_failed and not r.turn_completed and r.exit_code != 0, r; "
                "print('TRIAL_TOOL_OK: resume and rejection verified'); "
            )
        summary = (
            "" if transport_mode == "acp" else
            "print(r.output); print(r.error); raise SystemExit(0 if r.ok else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-c", "from argus_skill.core.knob_store import read_persisted_knobs; "
             "k=read_persisted_knobs(); assert k['ARGUS_SKILL_MODEL']=='gpt-5.5'; "
             "assert k['ARGUS_SKILL_ENGINEER_REASONING_EFFORT']=='high'; "
             "import shutil; " + probe + summary],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0 and "TRIAL_TOOL_OK" in result.stdout, result.stdout + result.stderr + str(rejected)
        assert len(requests) >= 3
        # Setup consumed one call; the local tool round trip consumed two.
        response = httpx.get(f"http://127.0.0.1:{port}/trial/status", headers={"Authorization": "Bearer " + config["api_key"]})
        paid_requests = len(requests) - (1 if transport_mode == "acp" else 0)
        assert response.json()["tokens_used"] == 120 * paid_requests
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
