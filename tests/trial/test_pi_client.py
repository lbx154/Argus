from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet
from pydantic import ValidationError

from argus_skill.trial import gateway
from argus_skill.trial.secrets import Vault, write_private


@pytest.mark.e2e
@pytest.mark.skipif(shutil.which("pi") is None, reason="Pi CLI required for real client smoke test")
def test_real_pi_chat_completions_text_tools_and_follow_up(tmp_path, monkeypatch):
    """Real Pi custom provider -> HTTP trial gateway -> simulated Responses."""
    state, home, agent = (tmp_path / name for name in ("server", "home", "agent"))
    for directory in (state, home, agent):
        directory.mkdir()
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("fake-github-secret")
    requests, upstream_requests, rejected, runs = [], [], [], []
    original_prepare = gateway.prepare

    def inspect_payload(data, model):
        requests.append(data)
        try:
            gateway.Completion.model_validate(data)
        except ValidationError as exc:
            rejected.extend(exc.errors(include_input=False))
        return original_prepare(data, model)

    monkeypatch.setattr(gateway, "prepare", inspect_payload)
    evidence, result_file = tmp_path / "evidence.txt", tmp_path / "result.txt"
    evidence.write_text("pi-local-read-evidence\n")
    tool_arguments = {
        "read": {"path": str(evidence)},
        "write": {"path": str(result_file), "content": "pi-local-write-evidence\n"},
        "edit": {
            "path": str(result_file),
            "edits": [{"oldText": "pi-local-write-evidence", "newText": "pi-local-edit-evidence"}],
        },
    }

    def upstream(request):
        assert str(request.url) == "https://api.githubcopilot.com/responses"
        payload = json.loads(request.content)
        upstream_requests.append(payload)
        assert payload["model"] == "gpt-5.5" and payload["reasoning"] == {"effort": "high"}
        assert payload["stream"] is True
        messages = payload["input"]
        user_index = max(i for i, message in enumerate(messages) if message.get("role") == "user")
        prompt = messages[user_index]["content"]
        outputs = [message for message in messages[user_index + 1:] if message.get("type") == "function_call_output"]
        text = None
        if "PI_REJECT_REQUEST" in prompt:
            return httpx.Response(400, json={"error": {"message": "Invalid Pi request fixture"}})
        if "PI_FOLLOW_UP" in prompt:
            for marker in ("PI_PLAIN_OK", "PI_READ_OK", "PI_WRITE_OK", "PI_EDIT_OK"):
                assert any(marker in str(message.get("content")) for message in messages)
            text = "PI_FOLLOW_UP_OK"
        elif "PI_PLAIN" in prompt:
            text = "PI_PLAIN_OK"
        else:
            tool_name = next(name for name in tool_arguments if f"PI_{name.upper()}" in prompt)
            tools = {tool["name"]: tool for tool in payload["tools"]}
            assert set(tools) == set(tool_arguments)
            assert tools[tool_name]["type"] == "function"
            assert set(tools[tool_name]["parameters"]["required"]) <= tool_arguments[tool_name].keys()
            if not outputs:
                output = [{
                    "type": "function_call",
                    "call_id": f"call_{tool_name}",
                    "name": tool_name,
                    "arguments": json.dumps(tool_arguments[tool_name]),
                }]
            else:
                assert len(outputs) == 1 and outputs[0]["call_id"] == f"call_{tool_name}"
                if tool_name == "read":
                    assert "pi-local-read-evidence" in outputs[0]["output"]
                else:
                    assert result_file.read_text() == f"pi-local-{tool_name}-evidence\n"
                    assert "error" not in outputs[0]["output"].lower()
                text = f"PI_{tool_name.upper()}_OK"
        if text is not None:
            output = [{"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": text}]}]
        response = {
            "id": "pi-test-response", "created_at": 1, "status": "completed", "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }
        content = "data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n"
        return httpx.Response(200, text=content, headers={"Content-Type": "text/event-stream"})

    app = gateway.create_app(gateway.Settings(state, key), transport=httpx.MockTransport(upstream))
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    # Installed Pi docs/models.md documents this custom provider and env interpolation.
    config = {"providers": {"argus-local": {
        "baseUrl": f"http://127.0.0.1:{port}/v1",
        "api": "openai-completions",
        "apiKey": "$ARGUS_PI_TEST_KEY",
        "headers": {"User-Agent": "Argus/0.1.1"},
        "models": [{"id": "argus-trial", "reasoning": True}],
    }}}
    (agent / "models.json").write_text(json.dumps(config, indent=2))
    # No inherited credentials, user extensions, project context, or startup networking.
    env = {
        "PATH": os.environ["PATH"], "HOME": str(home), "PI_CODING_AGENT_DIR": str(agent),
        "PI_OFFLINE": "1", "CI": "true",
    }
    command = [
        shutil.which("pi"), "--provider", "argus-local", "--model", "argus-trial",
        "--thinking", "high", "--mode", "json", "--print",
        "--session", str(tmp_path / "session.jsonl"), "--tools", "read,write,edit",
        "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
        "--no-context-files", "--no-approve",
        "--system-prompt", "You are a local compatibility test. Use only the requested local tools.",
    ]
    prompts = [
        ("PI_PLAIN: Return PI_PLAIN_OK.", "PI_PLAIN_OK"),
        (f"PI_READ: Read {evidence} and return PI_READ_OK.", "PI_READ_OK"),
        (f"PI_WRITE: Write pi-local-write-evidence to {result_file} and return PI_WRITE_OK.", "PI_WRITE_OK"),
        (f"PI_EDIT: Edit {result_file} to pi-local-edit-evidence and return PI_EDIT_OK.", "PI_EDIT_OK"),
        ("PI_FOLLOW_UP: Recall the previous text and tool results.", "PI_FOLLOW_UP_OK"),
        ("PI_REJECT_REQUEST", None),
    ]
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        credential = app.state.vault.credential("pi-test-key")
        app.state.store.issue("pi-test-key", credential)
        env["ARGUS_PI_TEST_KEY"] = credential
        for prompt, expected in prompts:
            result = subprocess.run(
                [*command, prompt], cwd=tmp_path, env=env,
                capture_output=True, text=True, timeout=60,
            )
            runs.append({
                "prompt": prompt, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
            })
            events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            messages = [event["message"] for event in events if event.get("type") == "message_end"
                        and event["message"].get("role") == "assistant"]
            if expected is None:
                # Pi 0.84.1 JSON mode exits zero on provider errors; inspect the event.
                assert any(message.get("stopReason") == "error"
                           and "provider_rejected_request" in message.get("errorMessage", "")
                           for message in messages), runs[-1]
            else:
                assert result.returncode == 0, {**runs[-1], "rejected": rejected}
                assert any(expected == part.get("text") for message in messages
                           for part in message["content"]), runs[-1]
        assert result_file.read_text() == "pi-local-edit-evidence\n"
        assert len(requests) == len(upstream_requests) == 9
        assert not rejected
        assert app.state.store.status("pi-test-key")["tokens_used"] == 8 * 120
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
