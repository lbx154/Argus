"""Real Argus trial client against the real hosted gateway."""
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

from argus_trial_server.gateway import Settings, create_app
from argus_trial_server.secrets import Vault, write_private


@pytest.mark.e2e
@pytest.mark.skipif(shutil.which("copilot") is None, reason="Copilot CLI required for real client smoke test")
@pytest.mark.parametrize("local_tool", ["view", "write"])
def test_real_argus_setup_and_copilot_tool_round_trip(tmp_path, monkeypatch, local_tool):
    """Real Argus -> real Copilot CLI -> HTTP gateway -> simulated upstream."""
    key = tmp_path / "key"
    state = tmp_path / "server"
    state.mkdir()
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("fake-github-secret")
    requests = []
    rejected = []
    from pydantic import ValidationError

    from argus_trial_server import gateway

    original_prepare = gateway.prepare

    def inspect_payload(data, model, *, models=()):
        try:
            gateway.Completion.model_validate(data)
        except ValidationError as exc:
            rejected.extend(exc.errors(include_input=False))
            rejected.append({"snippy": data.get("snippy")})
        return original_prepare(data, model, models=models)

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
        if any("ARGUS_SETUP_OK" in str(m.get("content")) for m in messages):
            delta, finish = {"role": "assistant", "content": "ARGUS_SETUP_OK"}, "stop"
        elif any(m.get("type") in {"function_call_output", "custom_tool_call_output"} for m in messages):
            if local_tool == "view":
                assert any("trial-local-file-evidence" in str(m.get("output")) for m in messages if m.get("type") == "function_call_output")
            else:
                assert any(m.get("type") in {"custom_tool_call_output", "function_call_output"} for m in messages)
                assert (tmp_path / "result.txt").read_text() == "trial-local-patch-evidence\n"
            delta, finish = {"role": "assistant", "content": "TRIAL_TOOL_OK"}, "stop"
        elif local_tool == "write":
            # CLI versions expose either custom apply_patch or function create
            # for opaque hosted model selectors. Exercise the advertised tool.
            tool = next((t for t in payload["tools"] if t["name"] == "apply_patch"), None)
            if tool is not None:
                assert tool["type"] == "custom" and tool["format"]["type"] == "grammar"
                assert tool["format"]["syntax"] == "lark" and tool["format"]["definition"]
                assert "grammar" not in tool["format"]
                call = {"id": "call_patch", "type": "custom", "custom": {
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Add File: result.txt\n+trial-local-patch-evidence\n*** End Patch",
                }}
            else:
                assert any(t["name"] == "create" and t["type"] == "function" for t in payload["tools"])
                call = {"id": "call_create", "type": "function", "function": {
                    "name": "create", "arguments": json.dumps({"path": str(tmp_path / "result.txt"), "file_text": "trial-local-patch-evidence\n"}),
                }}
            delta = {"role": "assistant", "tool_calls": [call]}
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
    credential = app.state.store.issue("test-key")
    project = Path(__file__).resolve().parents[2]
    # Isolate Copilot/Argus state and remove all inherited provider credentials and
    # role overrides. Preserve HOME itself rather than repurposing it.
    env = {k: v for k, v in os.environ.items() if not (
        k.startswith(("ARGUS_", "PI_", "COPILOT_", "GH_", "GITHUB_")) or "TOKEN" in k or "API_KEY" in k
    )}
    env.update(
        ARGUS_SKILL_HOME=str(tmp_path / "argus"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        PYTHONPATH=str(project),
        PYTHONIOENCODING="utf-8",
        CI="true",
        ARGUS_TRIAL_KEY=credential,
    )
    (tmp_path / "evidence.txt").write_text("trial-local-file-evidence")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "argus", "--setup", "--trial-url", f"http://127.0.0.1:{port}"],
            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr + str(rejected)
        config = json.loads((tmp_path / "argus/copilot-trial.json").read_text())
        assert config["api_key"].startswith("argus_trial_")
        assert "fake-github-secret" not in json.dumps(config)
        assert "fake-access-secret" not in json.dumps(config)
        env.pop("ARGUS_TRIAL_KEY")
        # A fresh Python process proves persisted trial routing; this goes
        # through Argus's actual worker launch, not a manually configured CLI.
        # The readiness probe owns an unrelated temporary sandbox. This file
        # round trip must explicitly grant the fixture's working directory.
        probe = (
            "from argus.adapters.agent_cli_backend import AgentCliBackend; "
            "from argus.core.models import RunnerOptions; "
            "from argus.core.run_gateway import run_exec; "
            "runner=AgentCliBackend(backend='copilot', runner_bin=shutil.which('copilot')); "
            f"o=RunnerOptions(model='argus-trial', working_dir={str(tmp_path)!r}, "
            "sandbox_mode='read-only', force_safe_mode=True, skip_git_repo_check=True); "
            "r=run_exec(runner, resume_thread_id=None, options=o, run_label='trial-tool-smoke', "
            f"prompt={'Read ' + str(tmp_path / 'evidence.txt') + ' and report TRIAL_TOOL_OK.'!r}); "
            "assert r.exit_code == 0 and not r.fatal_error and r.tool_activity_observed, r; "
            "assert 'TRIAL_TOOL_OK' in r.last_agent_message, r; "
            if local_tool == "view" else
            "from argus.core.agent_probe import run_agent_repair_prompt; "
            "r=run_agent_repair_prompt(backend='copilot', executable=shutil.which('copilot'), "
            f"working_dir={str(tmp_path)!r}, model='argus-trial', run_label='trial-tool-smoke', "
            "prompt='Create result.txt with trial-local-patch-evidence using apply_patch, then report TRIAL_TOOL_OK.'); "
            "assert r.ok and 'TRIAL_TOOL_OK' in r.output, r; "
        )
        probe += (
            "from argus.core.agent_probe import run_read_only_agent_prompt; "
            "r=run_read_only_agent_prompt(backend='copilot',executable=shutil.which('copilot'),"
            "model='argus-trial',run_label='trial-reject-smoke',prompt='TRIAL_REJECT_REQUEST'); "
            "assert not r.ok and r.error, r; print('TRIAL_TOOL_OK: one-shot rejection verified'); "
        )
        result = subprocess.run(
            [sys.executable, "-c", "from argus.core.knob_store import read_persisted_knobs; "
             "k=read_persisted_knobs(); assert k['ARGUS_SKILL_MODEL']=='argus-trial'; "
             "assert k['ARGUS_SKILL_ENGINEER_REASONING_EFFORT']=='high'; "
             "import shutil; " + probe],
            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert result.returncode == 0 and "TRIAL_TOOL_OK" in result.stdout, result.stdout + result.stderr + str(rejected)
        assert len(requests) >= 3
        # Setup consumed one call; the local tool round trip consumed two.
        response = httpx.get(f"http://127.0.0.1:{port}/trial/status", headers={"Authorization": "Bearer " + config["api_key"]})
        paid_requests = len(requests) - 1
        assert response.json()["tokens_used"] == 120 * paid_requests
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
