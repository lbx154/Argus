"""Offline native-schema transport checks; fixtures are not collected user data."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from argus.agent_cli import _structured_output as transport
from argus.agent_cli.agent_cli_runner import (
    AgentCliRunner,
    RunnerOptions,
)

SCHEMA = {
    "type": "object", "properties": {"answer": {"$ref": "#/$defs/answer"}},
    "required": ["answer"], "additionalProperties": False,
    "$defs": {"answer": {"type": "string"}},
}


# A hosting package's capture extension, which Pi loads after the schema one.
CAPTURE = "/hosting/capture.mjs"


def options(**kwargs):
    return RunnerOptions(disable_tools=True, output_schema=SCHEMA, **kwargs)


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
    first = options(_training_extension=CAPTURE)
    second = dataclasses.replace(first, output_schema={**SCHEMA, "description": "another call"})
    command = runner._build_command(resume_thread_id=None, options=first)
    assert command.index(str(transport.PI_OUTPUT_SCHEMA_EXTENSION)) < command.index(CAPTURE)
    assert "--no-tools" in command and "--no-extensions" in command
    assert json.loads(runner._child_env(first)[transport.PI_OUTPUT_SCHEMA_ENV]) == SCHEMA
    assert json.loads(runner._child_env(second)[transport.PI_OUTPUT_SCHEMA_ENV]) == second.output_schema
    assert os.environ[transport.PI_OUTPUT_SCHEMA_ENV] == "ambient-value-must-not-apply"

    ordinary = RunnerOptions(_training_extension=CAPTURE)
    command = runner._build_command(resume_thread_id=None, options=ordinary)
    assert str(transport.PI_OUTPUT_SCHEMA_EXTENSION) not in command
    assert CAPTURE in command and "--no-tools" not in command
    assert transport.PI_OUTPUT_SCHEMA_ENV not in runner._child_env(ordinary)
    monkeypatch.setattr(transport, "PI_OUTPUT_SCHEMA_EXTENSION", tmp_path / "missing.mjs")
    with pytest.raises(ValueError, match="extension is unavailable"):
        runner._build_command(resume_thread_id=None, options=first)


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
        if os.name != "nt":  # Windows protects files with ACLs, not POSIX mode bits.
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
