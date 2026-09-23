"""Opt-in real Pi CLI tests. All provider requests use a local HTTP fixture."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PI_CLI = os.environ.get("ARGUS_PI_TEST_CLI")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not PI_CLI or not shutil.which("node") or not (ROOT / "packages/runtime/dist/pi.js").is_file(),
    reason="set ARGUS_PI_TEST_CLI and build Node packages for the local native-CLI fixture",
)]


def test_real_typescript_pi_schema_and_plugin_transport_use_only_local_provider(tmp_path, monkeypatch):
    requests = []

    class HTTP(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"id": "fixture", "model": "offline", "choices": [{"index": 0, "delta": {
                    "role": "assistant", "content": '{"answer":"local native fixture"}'}, "finish_reason": None}]},
                {"id": "fixture", "model": "offline", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            self.wfile.write("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks).encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    agent = tmp_path / "agent"
    agent.mkdir()
    providers = {name: {"baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": api,
                       "apiKey": "offline-fixture-key", "models": [{"id": "offline", "reasoning": False}]}
                 for name, api in [("argus-local", "openai-completions"), ("argus-unsupported", "anthropic-messages")]}
    (agent / "models.json").write_text(json.dumps({"providers": providers}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("PI_OFFLINE", "1")
    monkeypatch.setenv("PI_HARNESS_PROFILE", "argus")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus"))
    monkeypatch.setenv("ARGUS_PI_OUTPUT_SCHEMA", "ambient-must-not-apply")

    def run(model="argus-local/offline", **request):
        result = subprocess.run([shutil.which("node"), str(ROOT / "packages/runtime/fixtures/native-pi-run.mjs")],
                                input=json.dumps({"executable": PI_CLI, "python": sys.executable,
                                                  "sourceRoot": str(ROOT), "cwd": str(tmp_path),
                                                  "model": model, "request": request}),
                                capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
        return json.loads(result.stdout)

    schema = {"type": "object", "properties": {"answer": {"$ref": "#/$defs/answer"}},
              "required": ["answer"], "additionalProperties": False, "$defs": {"answer": {"type": "string"}}}
    plugin = tmp_path / "trusted-plugin.mjs"
    plugin.write_text('''export default function(pi) {
  pi.registerTool({name:'argus_probe',label:'Probe',description:'Offline fixture',
    parameters:{type:'object',properties:{}},
    async execute() { return {content:[{type:'text',text:'fixture'}],details:{}}; }});
  pi.on('before_provider_request', event => ({...event.payload,
    metadata:{argus_fixture:process.env.ARGUS_PLUGIN_FIXTURE}}));
}
''')
    try:
        structured = run(outputSchema=schema)
        assert structured["turnCompleted"], structured
        assert structured["agentMessages"] == ['{"answer":"local native fixture"}']
        assert requests[-1]["response_format"]["json_schema"]["schema"] == schema
        assert not requests[-1].get("tools")

        enabled = run(toolPolicy="read-only", trustedExtensions=[str(plugin)], trustedToolNames=["argus_probe"],
                      extensionEnv={"ARGUS_PLUGIN_FIXTURE": "explicit-plugin-value"})
        assert enabled["turnCompleted"], enabled
        names = {tool["function"]["name"] for tool in requests[-1]["tools"]}
        assert "argus_probe" in names and not names.intersection({"bash", "write", "edit"})
        assert requests[-1]["metadata"] == {"argus_fixture": "explicit-plugin-value"}
        assert "response_format" not in requests[-1]

        plain = run(trustedExtensions=[str(plugin)], extensionEnv={"ARGUS_PLUGIN_FIXTURE": "must-not-load"})
        assert plain["turnCompleted"], plain
        assert "response_format" not in requests[-1] and not requests[-1].get("tools")
        assert "metadata" not in requests[-1]

        before = len(requests)
        rejected = run(model="argus-unsupported/offline", outputSchema=schema)
        assert not rejected["turnCompleted"] and rejected["exitCode"] != 0
        assert len(requests) == before
        assert os.environ["ARGUS_PI_OUTPUT_SCHEMA"] == "ambient-must-not-apply"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
