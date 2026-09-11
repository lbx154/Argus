"""Synthetic, isolated IPC fixtures; the optional Pi test uses a fake provider.

These validate the real collector and tool execution, never count as user data
or a live model acceptance result. No existing tenant state is opened.
"""
import io
import json
import os
import re
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.trial import training_bridge as bridge_module
from argus_skill.trial import training_runtime as runtime
from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.journey_journal import Journal
from argus_skill.trial.research_controls import ResearchControls
from argus_skill.trial.store import Store
from argus_skill.trial.training_bridge import TrainingBridge, _Handler, _Server
from argus_skill.trial.training_capture import HOSTED_PROFILE
from argus_skill.trial.training_data import NOTICE_VERSION, TrainingData


@pytest.fixture
def training(tmp_path):
    analytics = Analytics(tmp_path / "analytics", {"tenant-one": {"data_dir": str(tmp_path / "tenant"), "internal_test": False}},
                          tmp_path / "trial.sqlite3", tmp_path / "compute.sqlite3")
    store = Store(analytics.trial_db)
    with store.transaction() as db:
        db.execute("INSERT INTO trial_keys(key_id,credential_hash) VALUES ('tenant-one','test-only')")
    analytics.record_consent("tenant-one", analytics.notice_version)
    root = tmp_path / "tenant/home/.argus-skill/projects/s-project"
    root.mkdir(parents=True)
    (root / "session.json").write_text(json.dumps({"id": "s-project"}))
    (root / "events.jsonl").touch()
    journal = Journal(analytics)
    data = TrainingData(analytics, journal, ResearchControls(analytics))
    data.set_permissions("tenant-one", {"notice_version": NOTICE_VERSION, "internal_training": True, "external_sharing": False})
    journal.poll("tenant-one")
    return data


def registration():
    return {"action": "register", "lease": None, "value": {
        "sid": "s-project", "call_id": "call-real", "run_label": "engineer-r1", "mission_id": "task-real",
        "command": ["argus-pi", "--no-extensions", "--no-context-files", "--extension",
                    str(bridge_module.IMAGE_PACKAGE / bridge_module.EXTENSION_NAME)],
    }}


def verifier(peer, *, producer=False, parent=None):
    if producer and peer[0] != 20:
        raise AnalyticsError(403, "training_peer_parent_mismatch")
    if not producer and peer[0] != 10:
        raise AnalyticsError(403, "training_runtime_parent_untrusted")
    return {"pid": peer[0], "started": "real-start-tick", "source_sha256": {"test_fixture": "a" * 64}}


def test_bridge_binds_parent_producer_and_episode_and_reports_failures(training):
    bridge = TrainingBridge(training, "tenant-one", verifier)
    with pytest.raises(AnalyticsError, match="runtime_parent_untrusted"):
        bridge.dispatch(registration(), (99, 0, 0))
    lease = bridge.dispatch(registration(), (10, 0, 0))["lease"]
    with pytest.raises(AnalyticsError, match="peer_parent_mismatch"):
        bridge.dispatch({"action": "authorize", "lease": lease, "value": {}}, (99, 0, 0))
    result = bridge.dispatch({"action": "begin", "lease": lease, "value": {"session_id": "real-pi-id"}}, (20, 0, 0))
    assert result["profile"] == HOSTED_PROFILE
    with pytest.raises(AnalyticsError, match="binding_mismatch"):
        bridge.dispatch({"action": "event", "lease": lease, "value": {
            "episode_id": result["episode_id"] + 1, "kind": "settled", "payload": {},
        }}, (20, 0, 0))
    bridge.dispatch({"action": "close", "lease": lease, "value": {}}, (10, 0, 0))
    with training.analytics._db() as db:
        row = db.execute("SELECT state,reason,runtime_metadata FROM training_tool_episodes").fetchone()
    assert row["state"] == "quarantined" and row["reason"] == "runtime_call_unsettled"
    metadata = json.loads(row["runtime_metadata"])
    assert metadata["mission_id"] == "task-real" and len(metadata["capture_id"]) == 32
    status = bridge.status()
    assert status["counts"]["registered"] == status["counts"]["registration_failed"] == 1
    assert status["counts"]["producer_verification_failed"] == 1
    assert lease not in json.dumps(status)


def test_runtime_outage_and_unconsented_calls_preserve_original_options(monkeypatch):
    options = SimpleNamespace(disable_tools=False, isolate_workdir=False, extra_args=[])
    backend = SimpleNamespace(_backend_name="pi", _default_extra_args=[],
                              _runner=SimpleNamespace(_build_command=lambda **kw: ["argus-pi"]))
    ctx = SimpleNamespace(backend=backend, resume_thread_id=None, call_id="actual-call", run_label="engineer",
                          usage_mission_id=None, usage_project_root=None)
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv(runtime.SOCKET_ENV, "/test-only/unavailable.sock")
    monkeypatch.setattr(runtime, "_project", lambda ctx: "s-project")
    with runtime.capture_runtime_call(ctx, options):
        assert getattr(options, "_training_extension", None) is None
    monkeypatch.setattr(runtime, "_request", lambda *args: {"enabled": False})
    with runtime.capture_runtime_call(ctx, options):
        assert getattr(options, "_training_environment", None) is None


def test_parent_binding_rejects_tool_spawned_python_despite_argus_label(monkeypatch):
    peer = bridge_module.PeerVerifier.__new__(bridge_module.PeerVerifier)
    peer.web_uds = "/synthetic-only/web.sock"
    table = {100: {"pid": 100, "parent": 1}, 101: {"pid": 101, "parent": 100},
             102: {"pid": 102, "parent": 101}, 103: {"pid": 103, "parent": 102}}

    class Endpoint:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def settimeout(self, _value):
            pass

        def connect(self, _value):
            pass

        def getsockopt(self, *_args):
            return struct.pack("3i", 100, os.getuid(), os.getgid())

    monkeypatch.setattr(bridge_module.socket, "socket", lambda *_args: Endpoint())
    monkeypatch.setattr(bridge_module, "_process", lambda pid: table[pid])
    monkeypatch.setattr(bridge_module.os, "readlink", lambda path: "/usr/bin/node" if int(path.parent.name) == 102 else "/usr/bin/python3.11")
    peer._runtime_parent(table[101])
    with pytest.raises(AnalyticsError, match="runtime_parent_untrusted"):
        peer._runtime_parent({**table[103], "argv": ["python", "-c", "malicious", "argus_skill"]})


def test_extension_excludes_private_blocks_and_provider_reencoding_before_ipc(tmp_path):
    extension = Path(runtime.EXTENSION).as_uri()
    script = r'''
import {trainingExtension} from __EXTENSION__;
const handlers=new Map(), receipts=[];
const tools=[{name:'bash',description:'Execute a command.',parameters:{type:'object'}}];
trainingExtension(async(action,value)=>{receipts.push({action,value});return action==='authorize'?{enabled:true}:
 action==='begin'?{episode_id:1,profile:'pi-0.85.1-hosted-workspace-v1',allowed_tools:['bash']}:{state:'capturing'};})(
 {on:(name,handler)=>handlers.set(name,handler),getActiveTools:()=>['bash'],getAllTools:()=>tools});
const ctx={model:{provider:'argus',api:'openai-completions'},sessionManager:{getSessionId:()=> 'real-session'}};
await handlers.get('agent_start')({},ctx);
const user={role:'user',content:'Explain the experiment.',timestamp:Date.now()};
const assistant={role:'assistant',content:[{type:'thinking',thinking:'PRIVATE_NEVER_CAPTURE'},
 {type:'toolCall',id:'actual-call',name:'bash',arguments:{command:'printf 5'},thoughtSignature:'PRIVATE_SIGNATURE'}],
 timestamp:Date.now(),stopReason:'toolUse'};
const result={role:'toolResult',toolName:'bash',toolCallId:'actual-call',content:[{type:'text',text:'5'}],isError:false,timestamp:Date.now()};
await handlers.get('context')({messages:[user,assistant,result]});
await handlers.get('before_provider_request')({payload:{model:'unit-test',tools:[],messages:[
 {role:'system',content:'SYSTEM_NEVER_CAPTURE'}, {role:'user',content:'Explain the experiment.'},
 {role:'assistant',content:'PRIVATE_REENCODED_REASONING',tool_calls:[{id:'actual-call',type:'function',function:{name:'bash',arguments:'{"command":"printf 5"}'}}]},
 {role:'tool',tool_call_id:'actual-call',content:'5'}]}});
console.log(JSON.stringify(receipts));
'''.replace("__EXTENSION__", json.dumps(extension))
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True, capture_output=True, check=True, timeout=10)
    assert "PRIVATE_" not in result.stdout and "SYSTEM_NEVER" not in result.stdout
    receipts = json.loads(result.stdout)
    assert receipts[-1]["value"]["payload"] == {"reason": "provider_context_mismatch"}
    assert receipts[-1]["value"]["kind"] == "quarantine"
    context = next(item["value"]["payload"] for item in receipts if item["value"].get("kind") == "context")
    assert context["messages"][1]["content"][0]["type"] == "toolCall"


@pytest.fixture
def public_library_directory():
    # pytest's per-user directory may itself contain an email address. Such a
    # path is correctly rejected, so keep this synthetic public mount neutral.
    with tempfile.TemporaryDirectory(prefix="argus-public-library-") as directory:
        yield Path(directory)


@pytest.mark.skipif(not os.environ.get("ARGUS_TEST_PI_DIR"), reason="Set ARGUS_TEST_PI_DIR to the pinned local Pi source for real CLI integration")
@pytest.mark.parametrize("launch_mode", ["direct", "backend"])
def test_real_pi_cli_actual_provider_payload_and_bash_receipts(training, tmp_path, monkeypatch, launch_mode, public_library_directory):
    """Real pinned Pi + real bash + fake SSE provider, isolated from production."""
    provider_requests = []
    from argus_skill.trial import training_public_assets as public_assets

    # The host fixture remaps only the public library's filesystem mount. Pi
    # still performs the genuine read; body verification uses shipped bytes.
    library = public_library_directory
    asset_name = "_shared_verticals/software/engineer/software-change-implementation.md"
    public_body = public_assets.public_skill_body(public_assets.ROOT + "/" + asset_name)
    asset = library / asset_name
    asset.parent.mkdir(parents=True)
    asset.write_text(public_body)
    published_lookup = public_assets.public_skill_body
    monkeypatch.setattr(public_assets, "public_skill_body", lambda path: published_lookup(
        public_assets.ROOT + path[len(str(library)):] if isinstance(path, str) and path.startswith(str(library) + "/") else path,
    ))
    monkeypatch.setattr(public_assets, "_PATH", re.compile(
        "(?:" + re.escape(str(library)) + "|" + re.escape(public_assets.ROOT) + r")/[^\s\"'`<>()[\]{};,|]+",
    ))
    sid, mission = "s-a1b2c3d4", "a1b2c3d4e5f6"
    project = training.analytics.tenants["tenant-one"]["data_dir"] / "home/.argus-skill/projects" / sid
    project.mkdir()
    (project / "session.json").write_text(json.dumps({"id": sid}))
    (project / "events.jsonl").touch()
    training.journal.poll("tenant-one")

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            provider_requests.append(payload)
            if len(provider_requests) == 1:
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "call-real-read", "type": "function", "function": {
                        "name": "read", "arguments": json.dumps({"path": str(asset), "offset": None, "limit": None,
                                                                    "pages": None, "cells": None, "includeOutputs": None})}}]}
                finish = "tool_calls"
            elif len(provider_requests) == 2:
                delta = {"role": "assistant", "reasoning_content": "PRIVATE_SYNTHETIC_THINKING", "tool_calls": [{
                    "index": 0, "id": "call-real-bash", "type": "function", "function": {
                        "name": "bash", "arguments": json.dumps({"command": "printf 5 > result.txt; cat result.txt", "timeout": None})}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "Created result.txt; its content is 5."}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [{"id": "test-response", "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": "test-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "test-response", "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": "test-model", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}}]
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")

    http = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setattr(bridge_module, "IMAGE_PACKAGE", Path(runtime.EXTENSION).parent)
    bridge = TrainingBridge(training, "tenant-one", lambda peer, **kw: {
        "pid": peer[0], "started": "synthetic-peer-fixture", "source_sha256": {"fixture": "a" * 64}})
    server = _Server(str(tmp_path / "training.sock"), _Handler)
    server.bridge = bridge
    threading.Thread(target=server.serve_forever, daemon=True).start()
    request = registration()
    request["value"].update(sid=sid, mission_id=mission)
    lease = bridge.dispatch(request, (os.getpid(), os.getuid(), os.getgid()))["lease"] if launch_mode == "direct" else None
    agent_dir = tmp_path / "pi-config"
    agent_dir.mkdir()
    (agent_dir / "models.json").write_text(json.dumps({"providers": {"argus": {
        "baseUrl": f"http://127.0.0.1:{http.server_port}/v1", "api": "openai-completions", "apiKey": "synthetic-test-only",
        "models": [{"id": "test-model", "name": "Synthetic integration fixture", "reasoning": True,
                    "contextWindow": 128000, "maxTokens": 8192}]}}}))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cli = Path(os.environ["ARGUS_TEST_PI_DIR"]) / "packages/coding-agent/dist/bundle/cli.js"
    env = {**os.environ, "PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1",
           runtime.SOCKET_ENV: str(tmp_path / "training.sock")}
    if lease:
        env[runtime.LEASE_ENV] = lease
    from test_training_public_paths import hosted_engineer_prompt

    # Exercise the real public Engineer wrapper, including the hosted workdir
    # and generated checkpoint references absent from the original direct test.
    prompt = hosted_engineer_prompt("Write result.txt containing 5 and read it back.")
    launched = {}
    try:
        if launch_mode == "direct":
            result = subprocess.run(["node", str(cli), "--mode", "json", "--session-dir", str(tmp_path / "sessions"),
                                     "--no-extensions", "--extension", runtime.EXTENSION, "--no-skills", "--no-context-files",
                                     "--no-prompt-templates", "--no-themes", "--no-approve",
                                     "--tools", "read,write,edit,grep,find,ls,bash",
                                     "--model", "argus/test-model", "--thinking", "low"],
                                    cwd=workspace, env=env, input=prompt, text=True, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr[-2000:]
        else:
            from argus_skill.adapters.agent_cli_backend import AgentCliBackend
            from argus_skill.agent_cli import _run_exec
            from argus_skill.core.models import RunnerOptions

            for key, value in env.items():
                monkeypatch.setenv(key, value)
            monkeypatch.setenv("ARGUS_SKILL_HOME", str(project.parent.parent))
            monkeypatch.setenv("ARGUS_SKILL_PI_SESSION_DIR", str(tmp_path / "sessions"))
            monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
            monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "0")
            monkeypatch.delenv(runtime.LEASE_ENV, raising=False)
            backend = AgentCliBackend(backend="pi", runner_bin=str(cli), known_secret_values_override=())
            backend.set_usage_context(project_root=project, global_root=project.parent.parent, mission_id=mission + ":attempt:1")
            original_spawn = _run_exec.spawn_owned_process

            def observe_spawn(command, **kwargs):
                # Observe the actual Popen boundary after run_exec has applied
                # its dataclasses.replace sandbox policy; do not stub the runner.
                launched["extension_present"] = runtime.EXTENSION in command and "--extension" in command
                launched["lease"] = (kwargs.get("env") or {}).get(runtime.LEASE_ENV)
                return original_spawn(command, **kwargs)

            monkeypatch.setattr(_run_exec, "spawn_owned_process", observe_spawn)
            result = backend.run_exec(prompt=prompt, options=RunnerOptions(
                model="argus/test-model", reasoning_effort="low", working_dir=str(workspace),
                extra_args=["--tools", "read,write,edit,grep,find,ls,bash"],
            ), run_label="engineer-test")
            assert result.exit_code == 0, result.fatal_error
            assert launched["extension_present"] and launched["lease"]
            lease = launched["lease"]
            for log in project.rglob("*.jsonl"):
                assert lease not in log.read_text(errors="replace")
        assert (workspace / "result.txt").read_text() == "5"
        assert len(provider_requests) == 3
        with training.analytics._db() as db:
            row = db.execute("SELECT state,reason,record FROM training_tool_episodes").fetchone()
        assert row is not None and row["state"] == "complete", (dict(row) if row else None, bridge.status())
        assert "PRIVATE_SYNTHETIC_THINKING" not in row["record"]
        preview = training.preview("internal_training", [{"tenant_id": "tenant-one", "sid": sid}])
        assert preview["counts"]["tool_candidates"] == 1
        sample = preview["candidates"][0]
        assert sample["sample"]["tools"] == provider_requests[0]["tools"]
        assert len(sample["sample"]["tools"]) == 7
        assert sample["sample"]["messages"][1]["tool_calls"][0]["function"]["name"] == "read"
        assert sample["sample"]["messages"][2]["content"] == public_body
        assert sample["sample"]["messages"][3]["tool_calls"][0]["function"]["arguments"]["timeout"] is None
        assert sample["task_id"] == mission
        assert sample["sample"]["messages"][0]["content"] == prompt
        blob, _ = training.export("internal_training", [{"tenant_id": "tenant-one", "sid": sid}], review={
            "content_approved": True, "tool_context_approved": True, "approved_event_ids": [sample["event_id"]]})
        with zipfile.ZipFile(io.BytesIO(blob)) as package:
            assert len(package.read("sft_train.jsonl").splitlines()) == 1
            assert all(lease.encode() not in package.read(name) for name in package.namelist())
        from argus_skill.trial.training_validate import validate_package

        validation = validate_package(blob)
        assert validation["valid"], validation
    finally:
        server.shutdown()
        server.server_close()
        http.shutdown()
        http.server_close()
