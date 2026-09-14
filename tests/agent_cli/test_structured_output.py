"""Offline native-schema transport checks; fixtures are not collected user data."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import socketserver
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli import _structured_output as transport
from argus.agent_cli.agent_cli_runner import (
    AgentCliRunner,
    PrivateRunnerEnvironment,
    RunnerOptions,
)
from argus.core.models import RunnerOptions as CoreOptions
from argus.trial import training_runtime

SCHEMA = {
    "type": "object", "properties": {"answer": {"$ref": "#/$defs/answer"}},
    "required": ["answer"], "additionalProperties": False,
    "$defs": {"answer": {"type": "string"}},
}


def options(**kwargs):
    return RunnerOptions(disable_tools=True, output_schema=SCHEMA, **kwargs)


@pytest.mark.parametrize("backend", ["pi", "codex"])
def test_adapter_forwards_an_independent_schema_and_rejects_an_old_runner(backend, monkeypatch):
    runner = AgentCliBackend(backend=backend, runner_bin=backend)
    source = json.loads(json.dumps(SCHEMA))
    forwarded = runner._translate_options(CoreOptions(disable_tools=True, output_schema=source))
    assert forwarded.output_schema == SCHEMA
    source["$defs"]["answer"]["description"] = "Changed by another caller"
    assert forwarded.output_schema == SCHEMA
    assert runner._translate_options(CoreOptions()).output_schema is None

    @dataclasses.dataclass
    class OldOptions:
        model: str | None = None

    monkeypatch.setitem(runner._deps, "CliRunnerOptions", OldOptions)
    with pytest.raises(ValueError, match="does not support native output_schema"):
        runner._translate_options(CoreOptions(disable_tools=True, output_schema=SCHEMA))


@pytest.mark.parametrize("backend, call_options, message", [
    ("pi", RunnerOptions(output_schema=SCHEMA), "disable_tools"),
    ("codex", RunnerOptions(output_schema=SCHEMA), "disable_tools"),
    ("claude", options(), "not supported"),
    ("pi", RunnerOptions(disable_tools=True, output_schema=[]), "JSON Schema object"),
])
def test_invalid_structured_calls_fail_before_start(backend, call_options, message, monkeypatch):
    runner = AgentCliRunner(backend=backend, agent_bin=backend)
    spawn = []
    monkeypatch.setattr(runner, "_run_prepared_exec", lambda **kwargs: spawn.append(kwargs))
    with pytest.raises(ValueError, match=message):
        runner.run_exec(prompt="offline fixture", resume_thread_id=None, options=call_options)
    assert not spawn


def test_pi_schema_is_child_only_and_loaded_before_the_existing_capture_extension(tmp_path, monkeypatch):
    monkeypatch.setenv(transport.PI_OUTPUT_SCHEMA_ENV, "ambient-value-must-not-apply")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    runner = AgentCliRunner(backend="pi", agent_bin="pi")
    first = options(_training_extension=training_runtime.EXTENSION)
    second = dataclasses.replace(first, output_schema={**SCHEMA, "description": "another call"})
    command = runner._build_command(resume_thread_id=None, options=first)
    assert command.index(str(transport.PI_OUTPUT_SCHEMA_EXTENSION)) < command.index(training_runtime.EXTENSION)
    assert "--no-tools" in command and "--no-extensions" in command
    assert json.loads(runner._child_env(first)[transport.PI_OUTPUT_SCHEMA_ENV]) == SCHEMA
    assert json.loads(runner._child_env(second)[transport.PI_OUTPUT_SCHEMA_ENV]) == second.output_schema
    assert os.environ[transport.PI_OUTPUT_SCHEMA_ENV] == "ambient-value-must-not-apply"

    ordinary = RunnerOptions(_training_extension=training_runtime.EXTENSION)
    command = runner._build_command(resume_thread_id=None, options=ordinary)
    assert str(transport.PI_OUTPUT_SCHEMA_EXTENSION) not in command
    assert training_runtime.EXTENSION in command and "--no-tools" not in command
    assert transport.PI_OUTPUT_SCHEMA_ENV not in runner._child_env(ordinary)
    monkeypatch.setattr(transport, "PI_OUTPUT_SCHEMA_EXTENSION", tmp_path / "missing.mjs")
    with pytest.raises(ValueError, match="extension is unavailable"):
        runner._build_command(resume_thread_id=None, options=first)


@pytest.mark.parametrize("backend", ["pi", "codex"])
def test_tools_disabled_plain_text_call_omits_native_schema_transport(tmp_path, monkeypatch, backend):
    monkeypatch.setenv(transport.PI_OUTPUT_SCHEMA_ENV, "ambient-schema-must-not-apply")
    runner = AgentCliRunner(backend=backend, agent_bin=backend)
    call_options = RunnerOptions(disable_tools=True, output_schema=None, working_dir=str(tmp_path),
                                 _training_extension=training_runtime.EXTENSION)
    with transport.structured_output_call(backend, call_options) as prepared:
        assert prepared is call_options
        command = runner._build_command(resume_thread_id=None, options=prepared)
        assert "--output-schema" not in command
        assert str(transport.PI_OUTPUT_SCHEMA_EXTENSION) not in command
        if backend == "pi":
            assert "--no-tools" in command and training_runtime.EXTENSION in command
            assert transport.PI_OUTPUT_SCHEMA_ENV not in runner._child_env(prepared)
    assert not list(tmp_path.glob("argus-output-schema-*"))


@pytest.mark.parametrize("raises", [False, True])
def test_codex_schema_file_lives_for_the_call_and_is_cleaned_on_every_exit(tmp_path, monkeypatch, raises):
    runner = AgentCliRunner(backend="codex", agent_bin="codex")
    paths = []

    def execute(**kwargs):
        prepared = kwargs["options"]
        command = runner._build_command(resume_thread_id=None, options=prepared)
        path = Path(command[command.index("--output-schema") + 1])
        paths.append(path)
        assert json.loads(path.read_text()) == SCHEMA
        assert path.stat().st_mode & 0o777 == 0o600
        assert prepared.output_schema == SCHEMA
        if raises:
            raise RuntimeError("offline child failure")
        return "finished"

    monkeypatch.setattr(runner, "_run_prepared_exec", execute)
    call_options = options(working_dir=str(tmp_path))
    if raises:
        with pytest.raises(RuntimeError, match="offline child failure"):
            runner.run_exec(prompt="fixture", resume_thread_id=None, options=call_options)
    else:
        assert runner.run_exec(prompt="fixture", resume_thread_id=None, options=call_options) == "finished"
    assert call_options._output_schema_path is None
    assert len(paths) == 1 and not paths[0].exists() and not paths[0].parent.exists()


def test_extension_transforms_the_payload_before_real_capture_without_changing_other_fields():
    script = r'''
import extension, {SCHEMA_ENV} from __EXTENSION__;
import {trainingExtension} from __CAPTURE__;
const handlers = new Map(), receipts = [];
const pi = {on:(name, handler) => handlers.set(name,[...(handlers.get(name)||[]),handler]),
  getActiveTools:()=>[], getAllTools:()=>[]};
const schema=JSON.parse(process.env[SCHEMA_ENV]);
extension(pi);
if (process.env[SCHEMA_ENV] !== undefined) throw Error('schema escaped extension scope');
trainingExtension(async(action,value)=>{
  receipts.push({action,value});
  return action==='authorize'?{enabled:true}:action==='begin'?
    {episode_id:1,profile:'pi-0.85.1-hosted-workspace-v1'}:{state:'capturing'};
})(pi);
const ctx={model:{api:'openai-completions'},sessionManager:{getSessionId:()=> 'offline-session'}};
for (const handler of handlers.get('agent_start')) await handler({},ctx);
const original={model:'offline',messages:[{role:'user',content:'fixture'}],tools:[],
  reasoning_effort:'high',stream:true,stream_options:{include_usage:true},store:false,max_completion_tokens:64};
let payload=original;
for (const handler of handlers.get('before_provider_request')) {
  const replacement=await handler({payload},ctx);
  if (replacement!==undefined) payload=replacement;
}
console.log(JSON.stringify({payload, original, schema,
  captured:receipts.find(row=>row.value?.kind==='provider_request').value.payload}));
'''.replace("__EXTENSION__", json.dumps(transport.PI_OUTPUT_SCHEMA_EXTENSION.as_uri())).replace(
        "__CAPTURE__", json.dumps(Path(training_runtime.EXTENSION).as_uri()))
    run = subprocess.run(["node", "--input-type=module"], input=script, text=True, capture_output=True,
                         env={**os.environ, transport.PI_OUTPUT_SCHEMA_ENV: json.dumps(SCHEMA)}, timeout=15, check=True)
    result = json.loads(run.stdout)
    expected = {"type": "json_schema", "json_schema": {"name": "argus_output", "strict": True, "schema": SCHEMA}}
    assert result["payload"] == {**result["original"], "response_format": expected}
    assert result["captured"]["response_format"] == expected
    assert result["captured"]["messages"] == result["original"]["messages"]
    assert result["schema"] == SCHEMA


def test_native_responses_preserves_text_options_and_uses_text_format():
    script = '''
import extension from __EXTENSION__;
let handler;
extension({on:(_name, fn)=>{handler=fn;},getActiveTools:()=>[]});
const original={model:'native',input:[{role:'user',content:'fixture'}],tools:[],
  text:{verbosity:'low',format:{type:'text'}},reasoning:{effort:'high'},stream:true,store:false};
const payload=await handler({payload:original},{model:{api:'openai-responses'}});
console.log(JSON.stringify({payload,original}));
'''.replace("__EXTENSION__", json.dumps(transport.PI_OUTPUT_SCHEMA_EXTENSION.as_uri()))
    run = subprocess.run(["node", "--input-type=module"], input=script, text=True, capture_output=True,
                         env={**os.environ, transport.PI_OUTPUT_SCHEMA_ENV: json.dumps(SCHEMA)}, timeout=15, check=True)
    result = json.loads(run.stdout)
    assert result["payload"] == {**result["original"], "text": {"verbosity": "low", "format": {
        "type": "json_schema", "name": "argus_output", "strict": True, "schema": SCHEMA}}}
    assert "response_format" not in result["payload"]
    assert result["original"]["text"]["format"] == {"type": "text"}


@pytest.mark.parametrize("schema, api, tools", [
    ("not-json", "openai-completions", []),
    ("[]", "openai-completions", []),
    (json.dumps(SCHEMA), "anthropic-messages", []),
    (json.dumps(SCHEMA), "openai-completions", ["bash"]),
])
def test_extension_errors_exit_even_when_pi_would_catch_handler_exceptions(schema, api, tools):
    script = '''
import extension from __EXTENSION__;
let handler;
extension({on:(_name, fn)=>{handler=fn;},getActiveTools:()=>__TOOLS__});
try { await handler({payload:{messages:[],tools:[]}}, {model:{api:__API__}}); } catch (_) {}
console.log('PROMPT_ONLY_REQUEST_WOULD_BE_SENT');
'''.replace("__EXTENSION__", json.dumps(transport.PI_OUTPUT_SCHEMA_EXTENSION.as_uri())).replace(
        "__TOOLS__", json.dumps(tools)).replace("__API__", json.dumps(api))
    run = subprocess.run(["node", "--input-type=module"], input=script, text=True, capture_output=True,
                         env={**os.environ, transport.PI_OUTPUT_SCHEMA_ENV: schema}, timeout=15)
    assert run.returncode == 1
    assert "Argus structured output:" in run.stderr
    assert "PROMPT_ONLY_REQUEST_WOULD_BE_SENT" not in run.stdout
    assert schema not in run.stderr


PI_CLI = os.environ.get("ARGUS_PI_TEST_CLI") or shutil.which("pi")


@pytest.mark.skipif(not PI_CLI or os.name != "posix", reason="Explicit local Pi bundle and Unix sockets required")
def test_real_pi_bundle_sends_native_schema_and_capture_observes_it_with_no_provider_network(tmp_path, monkeypatch):
    """Actual Pi CLI + real training extension; only local HTTP/IPC fixtures."""
    requests, receipts, failures = [], [], []
    response_text = ['{"answer":"offline fixture"}']

    class HTTP(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            if failures:
                status = failures.pop(0)
                body = json.dumps({"error": {"message": "429 TPM rate limit: offline retry fixture",
                                              "type": "rate_limit_error"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"id": "offline", "model": "offline-model", "choices": [{"index": 0, "delta": {
                    "role": "assistant", "content": response_text[0]}, "finish_reason": None}]},
                {"id": "offline", "model": "offline-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            self.wfile.write("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks).encode())
            self.wfile.write(b"data: [DONE]\n\n")

    class Bridge(socketserver.StreamRequestHandler):
        def handle(self):
            row = json.loads(self.rfile.readline())
            receipts.append(row)
            action = row["action"]
            reply = {"enabled": True} if action == "authorize" else (
                {"episode_id": 1, "profile": "pi-0.85.1-hosted-workspace-v1"} if action == "begin" else {"state": "capturing"})
            self.wfile.write(json.dumps(reply).encode() + b"\n")

    http = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
    socket_path = tmp_path / "capture.sock"
    bridge = socketserver.ThreadingUnixStreamServer(str(socket_path), Bridge)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (http, bridge)]
    for thread in threads:
        thread.start()
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(json.dumps({"providers": {"argus-offline-test": {
        "baseUrl": f"http://127.0.0.1:{http.server_port}/v1", "api": "openai-completions",
        "apiKey": "offline-fixture-key", "models": [{"id": "offline-model", "reasoning": False}],
    }, "argus-offline-unsupported": {
        "baseUrl": f"http://127.0.0.1:{http.server_port}/v1", "api": "anthropic-messages",
        "apiKey": "offline-fixture-key", "models": [{"id": "offline-model", "reasoning": False}],
    }}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("PI_OFFLINE", "1")
    monkeypatch.setenv("PI_HARNESS_PROFILE", "argus")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus"))
    runner = AgentCliRunner(backend="pi", agent_bin=PI_CLI)
    call_options = options(
        model="argus-offline-test/offline-model", working_dir=str(tmp_path),
        sandbox_mode="read-only", force_safe_mode=True,
        _training_extension=training_runtime.EXTENSION,
        _training_environment=PrivateRunnerEnvironment({training_runtime.SOCKET_ENV: str(socket_path),
                                                       training_runtime.LEASE_ENV: "offline-fixture-lease"}),
    )
    try:
        for schema in (SCHEMA, {**SCHEMA, "description": "second independent call"}):
            result = runner.run_exec(prompt="Return the offline fixture JSON.", resume_thread_id=None,
                                     options=dataclasses.replace(call_options, output_schema=schema))
            assert result.exit_code == 0 and not result.fatal_error, result.stderr_lines
            assert result.last_agent_message == '{"answer":"offline fixture"}'
            assert "--no-extensions" in result.command and "--no-tools" in result.command
            assert requests[-1]["response_format"] == {
                "type": "json_schema", "json_schema": {"name": "argus_output", "strict": True, "schema": schema}}
            captured = [row["value"]["payload"] for row in receipts
                        if row["action"] == "event" and row["value"]["kind"] == "provider_request"]
            assert captured[-1]["response_format"] == requests[-1]["response_format"]
            assert captured[-1]["messages"] == requests[-1]["messages"]
        assert len(requests) == 2

        native_markdown = "# Literal mathematics\n\n" + r"\[\frac{10}{7}=\lambda.\] Literal \u0005 stays literal."
        response_text[0] = native_markdown
        receipt_start = len(receipts)
        before = len(requests)
        markdown_options = dataclasses.replace(call_options, output_schema=None, disable_tools=True)
        result = runner.run_exec(prompt="Return the complete offline Markdown document with literal backslashes.",
                                 resume_thread_id=None, options=markdown_options)
        assert result.exit_code == 0 and result.turn_completed and not result.fatal_error, result.stderr_lines
        assert result.last_agent_message.encode() == native_markdown.encode()
        assert "\f" not in result.last_agent_message and "\x05" not in result.last_agent_message
        assert len(requests) == before + 1
        assert "response_format" not in requests[-1] and not requests[-1].get("tools")
        assert "--no-tools" in result.command and training_runtime.EXTENSION in result.command
        assert str(transport.PI_OUTPUT_SCHEMA_EXTENSION) not in result.command
        captured = [row["value"]["payload"] for row in receipts[receipt_start:]
                    if row["action"] == "event" and row["value"]["kind"] == "provider_request"]
        # Existing providerProjection represents omitted tools as an empty list.
        # All other provider fields, including exact messages, must match.
        assert captured == [{**requests[-1], "tools": requests[-1].get("tools") or []}]
        assistants = [message for row in receipts[receipt_start:]
                      if row["action"] == "event" and row["value"]["kind"] == "message_end"
                      for message in row["value"]["payload"]["messages"] if message.get("role") == "assistant"]
        assert len(assistants) == 1
        captured_text = "\n".join(part["text"] for part in assistants[0]["content"] if part.get("type") == "text")
        assert captured_text.encode() == result.last_agent_message.encode() == native_markdown.encode()
        response_text[0] = '{"answer":"offline fixture"}'

        ordinary = dataclasses.replace(call_options, output_schema=None, disable_tools=False)
        result = runner.run_exec(prompt="Return the offline fixture without tools.", resume_thread_id=None, options=ordinary)
        assert result.exit_code == 0 and not result.fatal_error, result.stderr_lines
        assert "response_format" not in requests[-1]
        assert requests[-1]["tools"]
        captured = [row["value"]["payload"] for row in receipts
                    if row["action"] == "event" and row["value"]["kind"] == "provider_request"]
        assert "response_format" not in captured[-1] and captured[-1]["tools"]

        before = len(requests)
        receipt_start = len(receipts)
        failures.append(429)
        recovered = runner.run_exec(prompt="Recover once from the local TPM fixture.", resume_thread_id=None,
                                    options=ordinary)
        assert len(requests) == before + 2
        assert recovered.last_agent_message == '{"answer":"offline fixture"}'
        assert any(event.get("type") == "auto_retry_end" and event.get("success") is True
                   for event in recovered.json_events)
        # The real argus JSON profile preserves the original error as a
        # diagnostic, while deferring the runner's failure receipt until settled.
        assert any(event.get("type") == "attempt_error" and event.get("event", {}).get("type") == "message_end"
                   and event["event"]["message"]["stopReason"] == "error" for event in recovered.json_events)
        assert any(row["action"] == "event" and row["value"]["kind"] == "message_end"
                   and any(message.get("stopReason") == "error" for message in row["value"]["payload"]["messages"])
                   for row in receipts[receipt_start:])
        assert recovered.exit_code == 0 and recovered.turn_completed and not recovered.turn_failed and not recovered.fatal_error, {
            "exit_code": recovered.exit_code, "turn_completed": recovered.turn_completed,
            "turn_failed": recovered.turn_failed, "fatal_error": recovered.fatal_error,
        }

        before = len(requests)
        unsupported = dataclasses.replace(call_options, model="argus-offline-unsupported/offline-model")
        result = runner.run_exec(prompt="Must fail before local HTTP.", resume_thread_id=None, options=unsupported)
        assert result.exit_code != 0 or result.fatal_error
        assert any("does not support this provider API" in line for line in result.stderr_lines)
        assert len(requests) == before

        real_child_env = runner._child_env

        def broken_schema_transport(call_options, *, executable=None):
            env = real_child_env(call_options, executable=executable)
            env[transport.PI_OUTPUT_SCHEMA_ENV] = "invalid-json-transport-fixture"
            return env

        monkeypatch.setattr(runner, "_child_env", broken_schema_transport)
        result = runner.run_exec(prompt="Must fail during extension initialization.", resume_thread_id=None,
                                 options=call_options)
        assert result.exit_code != 0 or result.fatal_error
        assert any("required output schema could not be loaded" in line for line in result.stderr_lines)
        assert len(requests) == before
    finally:
        for server in (http, bridge):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
