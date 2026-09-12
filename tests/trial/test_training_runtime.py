"""Synthetic, isolated IPC fixtures; the optional Pi test uses a fake provider.

These validate the real collector and tool execution, never count as user data
or a live model acceptance result. No existing tenant state is opened.
"""
import json
import os
import re
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
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
from argus_skill.trial.training_bridge import HostPeerVerifier, TrainingBridge, _Handler, _Server
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
    assert row["state"] == "interrupted" and row["reason"] == "runtime_call_unsettled"
    metadata = json.loads(row["runtime_metadata"])
    assert metadata["mission_id"] == "task-real" and len(metadata["capture_id"]) == 32
    assert metadata["capture_policy"] == "retain-observed-v2"
    assert "source_sha256" not in metadata and "launch_sha256" not in metadata
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


@pytest.mark.parametrize("role,no_tools,isolated,resumed", [
    ("planner-bounded-plan", True, True, None),
    ("planner.cycle0", False, False, None),
    ("manager.reviewed_facts", True, True, None),
    ("engineer-r2", False, False, "existing-session"),
    ("reviewer", False, False, None),
    ("curator.distill", True, True, None),
])
def test_every_role_registers_text_only_isolated_and_resumed_calls(monkeypatch, role, no_tools, isolated, resumed):
    options = SimpleNamespace(disable_tools=no_tools, isolate_workdir=isolated, extra_args=[],
                              trusted_extensions=["/actual/plugin.mjs"], trusted_tool_names=["actual_plugin_tool"])
    launches, requests = [], []

    def build(**kwargs):
        launches.append(kwargs)
        assert kwargs["options"]._training_extension == runtime.EXTENSION
        return ["argus-pi", "--extension", runtime.EXTENSION]

    def request(path, action, value, lease=None):
        requests.append((action, value))
        return {"enabled": True, "lease": "synthetic-call-capability"}

    backend = SimpleNamespace(_backend_name="pi", _runner=SimpleNamespace(_build_command=build))
    ctx = SimpleNamespace(backend=backend, resume_thread_id=resumed, call_id="actual-call", run_label=role,
                          usage_mission_id="actual-task:attempt:2", usage_project_root=None)
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv(runtime.SOCKET_ENV, "/synthetic/training.sock")
    monkeypatch.setattr(runtime, "_project", lambda context: "s-project")
    monkeypatch.setattr(runtime, "_request", request)
    with runtime.capture_runtime_call(ctx, options):
        assert options._training_environment is not None
        assert options.disable_tools == no_tools and options.isolate_workdir == isolated
    assert launches[0]["resume_thread_id"] == resumed
    assert requests[0][1]["run_label"] == role
    assert requests[0][1]["mission_id"] == "actual-task"
    assert requests[-1][0] == "close"
    assert options._training_environment is None and options._training_extension is None


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


def test_extension_retains_actual_provider_input_and_excludes_structured_private_blocks(tmp_path):
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
 timestamp:Date.now(),stopReason:'toolUse',api:'openai-completions',provider:'argus',model:'unit-test',
 responseModel:'unit-test-version',responseId:'actual-response',providerThinkingLevel:'low',endTurn:false,
 usage:{input:30,output:10,reasoning:2,totalTokens:40,cost:{input:0.1,output:0.2,total:0.3},apiKey:'PRIVATE_USAGE_AUTH'},
 headers:{authorization:'PRIVATE_MESSAGE_AUTH'},deferred:{data:{token:'PRIVATE_DEFERRED'}}};
const result={role:'toolResult',toolName:'bash',toolCallId:'actual-call',content:[{type:'text',text:'5'}],isError:false,timestamp:Date.now()};
await handlers.get('context')({messages:[user,assistant,result]});
await handlers.get('before_provider_request')({payload:{model:'unit-test',tools:[],temperature:0.3,top_p:0.9,max_tokens:512,
 reasoning_effort:'low',stream:true,stream_options:{include_usage:true},headers:{authorization:'PRIVATE_PROVIDER_AUTH'},apiKey:'PRIVATE_KEY',messages:[
 {role:'system',content:'APPLICATION_SYSTEM_INSTRUCTIONS'}, {role:'user',content:'Explain the experiment.'},
 {role:'assistant',content:'Provider-transformed public content',tool_calls:[{id:'actual-call',type:'function',function:{name:'bash',arguments:'{"command":"printf 5"}'}}]},
 {role:'tool',tool_call_id:'actual-call',content:'5'}]}});
console.log(JSON.stringify(receipts));
'''.replace("__EXTENSION__", json.dumps(extension))
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True, capture_output=True, check=True, timeout=10)
    assert "PRIVATE_" not in result.stdout and "SYSTEM_NEVER" not in result.stdout
    receipts = json.loads(result.stdout)
    assert receipts[-1]["value"]["kind"] == "provider_request"
    assert receipts[-1]["value"]["payload"]["messages"][2]["content"] == "Provider-transformed public content"
    context = next(item["value"]["payload"] for item in receipts if item["value"].get("kind") == "context")
    assert context["messages"][1]["content"][0]["type"] == "toolCall"
    observed = context["messages"][1]
    assert observed["model"] == "unit-test" and observed["api"] == "openai-completions"
    assert observed["provider"] == "argus" and observed["responseId"] == "actual-response"
    assert observed["usage"] == {"input": 30, "output": 10, "reasoning": 2, "totalTokens": 40,
                                 "cost": {"input": 0.1, "output": 0.2, "total": 0.3}}
    provider = receipts[-1]["value"]["payload"]
    assert provider["temperature"] == 0.3 and provider["top_p"] == 0.9 and provider["max_tokens"] == 512
    assert provider["reasoning_effort"] == "low" and provider["stream_options"] == {"include_usage": True}
    assert "headers" not in provider and "apiKey" not in provider


def test_extension_keeps_text_only_planning_and_large_failed_custom_tool_results():
    script = r'''
import {trainingExtension} from __EXTENSION__;
const handlers=new Map(),receipts=[];
let active=[],nextEpisode=0;
trainingExtension(async(action,value)=>{
 receipts.push({action,value});
 if(action==='authorize')return {enabled:true};
 if(action==='begin')return {episode_id:++nextEpisode,profile:'pi-0.85.1-hosted-workspace-v1'};
 return {state:value.kind==='settled'?'complete':'capturing'};
})({on:(name,handler)=>handlers.set(name,handler),getActiveTools:()=>active,
 getAllTools:()=>[{name:'project_lookup',description:'Lookup project data',parameters:{type:'object'}}]});
const ctx={sessionManager:{getSessionId:()=> 'same-actual-session'}};
const user={role:'user',content:'Plan the project.',timestamp:Date.now()};
const answer={role:'assistant',content:[{type:'text',text:'First establish an oracle, then optimize and review.'}],stopReason:'stop'};
await handlers.get('agent_start')({},ctx);
await handlers.get('context')({messages:[user]});
await handlers.get('before_provider_request')({payload:{model:'actual-model',messages:[
 {role:'system',content:'You are the project planner.'},{role:'user',content:user.content}]}});
await handlers.get('agent_end')({messages:[user,answer]});
await handlers.get('agent_settled')({});
active=['project_lookup'];
await handlers.get('agent_start')({},ctx);
await handlers.get('context')({messages:[user,answer,{role:'user',content:'Continue.',timestamp:Date.now()}]});
await handlers.get('tool_call')({toolCallId:'real-tool',toolName:'project_lookup',input:{query:'current state'}});
await handlers.get('tool_result')({toolCallId:'real-tool',toolName:'project_lookup',input:{query:'current state'},
 content:[{type:'text',text:'x'.repeat(100000)}],isError:true,details:{truncation:{truncated:true}}});
await handlers.get('session_before_compact')({});
await handlers.get('agent_end')({messages:[user,answer]});
await handlers.get('agent_settled')({});
console.log(JSON.stringify(receipts));
'''.replace("__EXTENSION__", json.dumps(Path(runtime.EXTENSION).as_uri()))
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True,
                            capture_output=True, check=True, timeout=10)
    receipts = json.loads(result.stdout)
    starts = [item["value"] for item in receipts if item["action"] == "begin"]
    assert [item["allowed_tools"] for item in starts] == [[], ["project_lookup"]]
    events = [item["value"] for item in receipts if item["action"] == "event"]
    assert len([item for item in events if item["kind"] == "agent_end"]) == 2
    assert len([item for item in events if item["kind"] == "settled"]) == 2
    tool = next(item["payload"] for item in events if item["kind"] == "tool_result")
    assert tool["isError"] is True and tool["output_complete"] is False
    assert len(tool["content"][0]["text"]) == 100000
    assert any(item["kind"] == "capture_warning" for item in events)
    assert not any(item["kind"] == "quarantine" for item in events)


@pytest.fixture
def public_library_directory():
    # pytest's per-user directory may itself contain an email address. Such a
    # path is correctly rejected, so keep this synthetic public mount neutral.
    with tempfile.TemporaryDirectory(prefix="argus-public-library-") as directory:
        yield Path(directory)


@pytest.mark.skipif(not os.environ.get("ARGUS_TEST_PI_DIR"), reason="Set ARGUS_TEST_PI_DIR to the pinned local Pi source for real CLI integration")
@pytest.mark.parametrize("launch_mode", ["direct", "backend", "planner", "planner_read_only"])
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
            if launch_mode == "planner":
                delta = {"role": "assistant", "content": "Establish an independent oracle, implement the algorithm, then review."}
                finish = "stop"
            elif len(provider_requests) == 1:
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "call-real-read", "type": "function", "function": {
                        "name": "read", "arguments": json.dumps({"path": str(asset), "offset": None, "limit": None,
                                                                    "pages": None, "cells": None, "includeOutputs": None})}}]}
                finish = "tool_calls"
            elif launch_mode == "planner_read_only":
                delta = {"role": "assistant", "content": "PROJECT_DONE=yes\nREASON=Read the actual evidence file and completed the requested inspection."}
                finish = "stop"
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
    class WorkspaceSocket(socketserver.BaseRequestHandler):
        def handle(self):
            pass

    workspace_socket = socketserver.UnixStreamServer(str(tmp_path / "web.sock"), WorkspaceSocket)
    threading.Thread(target=workspace_socket.serve_forever, daemon=True).start()
    bridge = TrainingBridge(training, "tenant-one", HostPeerVerifier(
        "tenant-one", training.analytics.tenants["tenant-one"]["data_dir"], workspace_socket.server_address,
    ))
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
    prompt = ("Plan a rigorously verified scheduling solver." if launch_mode == "planner"
              else hosted_engineer_prompt("Write result.txt containing 5 and read it back."))
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
            monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1" if launch_mode == "planner_read_only" else "0")
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
            if launch_mode == "planner_read_only":
                from argus_skill.planner import Planner
                from argus_skill.planner.planner import PlannerConfig

                prompt = f"Read the actual evidence file {asset} and report the grounded inspection."
                verdict = Planner(backend, memory_maintenance_enabled=False).plan_next(
                    continuous_objective=prompt,
                    config=PlannerConfig(model="argus/test-model", reasoning_effort="low", working_dir=str(workspace)),
                )
                assert verdict.project_done and not verdict.error, verdict
            else:
                result = backend.run_exec(prompt=prompt, options=RunnerOptions(
                    model="argus/test-model", reasoning_effort="low", working_dir=str(workspace),
                    disable_tools=launch_mode == "planner", isolate_workdir=launch_mode == "planner",
                    extra_args=None if launch_mode == "planner" else ["--tools", "read,write,edit,grep,find,ls,bash"],
                ), run_label="planner-bounded-plan" if launch_mode == "planner" else "engineer-test")
                assert result.exit_code == 0, result.fatal_error
            assert launched["extension_present"] and launched["lease"]
            lease = launched["lease"]
            for log in project.rglob("*.jsonl"):
                assert lease not in log.read_text(errors="replace")
        if launch_mode not in {"planner", "planner_read_only"}:
            assert (workspace / "result.txt").read_text() == "5"
        assert len(provider_requests) == (1 if launch_mode == "planner" else 2 if launch_mode == "planner_read_only" else 3)
        with training.analytics._db() as db:
            row = db.execute("SELECT id,state,reason,runtime_metadata FROM training_tool_episodes").fetchone()
            events = training.capture.events(db, row["id"])
        assert row is not None and row["state"] == "complete", (dict(row) if row else None, bridge.status())
        assert "PRIVATE_SYNTHETIC_THINKING" not in json.dumps(events)
        assert lease not in json.dumps(events)
        requests = [event["payload"] for event in events if event["kind"] == "provider_request"]
        assert len(requests) == len(provider_requests)
        assert requests[0]["tools"] == provider_requests[0].get("tools", [])
        assert len(requests[0]["tools"]) == (0 if launch_mode == "planner" else 4 if launch_mode == "planner_read_only" else 7)
        if launch_mode == "planner_read_only":
            assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {"read", "grep", "find", "ls"}
        instructions = [message for message in requests[0]["messages"] if message["role"] in {"system", "developer"}]
        assert instructions == [message for message in provider_requests[0]["messages"]
                                if message["role"] in {"system", "developer"}]
        assert instructions
        calls = [event["payload"] for event in events if event["kind"] == "tool_call"]
        results = [event["payload"] for event in events if event["kind"] == "tool_result"]
        expected_tools = [] if launch_mode == "planner" else ["read"] if launch_mode == "planner_read_only" else ["read", "bash"]
        assert [call["toolName"] for call in calls] == expected_tools
        for kind in ("tool_execution_start", "tool_execution_end"):
            assert [event["payload"]["toolName"] for event in events if event["kind"] == kind] == expected_tools
        assert any(event["kind"] == "message_end" for event in events)
        assert any(event["kind"] == "message_delta" and event["payload"]["type"] == "text_delta" for event in events)
        if results:
            assert results[0]["content"][0]["text"] == public_body
        ending = next(event["payload"] for event in events if event["kind"] == "agent_end")
        assert ending["messages"][-1]["content"][0]["text"].strip()
        assert ending["messages"][-1]["model"] == "test-model"
        assert ending["messages"][-1]["provider"] == "argus"
        assert ending["messages"][-1]["usage"]["output"] == 10
        context = next(event["payload"] for event in events if event["kind"] == "context")
        assert prompt in context["messages"][0]["content"][0]["text"]
        metadata = json.loads(row["runtime_metadata"])
        assert metadata["mission_id"] == mission and metadata["capture_policy"] == "retain-observed-v2"
        if launch_mode == "planner":
            assert metadata["run_label"] == "planner-bounded-plan"
        elif launch_mode == "planner_read_only":
            assert metadata["run_label"] == "planner.cycle0"
        assert "source_sha256" not in metadata
    finally:
        server.shutdown()
        server.server_close()
        workspace_socket.shutdown()
        workspace_socket.server_close()
        http.shutdown()
        http.server_close()


def test_visible_stream_prefix_and_tool_execution_phases_survive_interruption(training):
    """Synthetic event source through the real extension and persistent receiver."""
    script = r'''
import {trainingExtension} from __EXTENSION__;
const handlers=new Map(),receipts=[];
trainingExtension(async(action,value)=>{
 if(action==='authorize')return {enabled:true};
 if(action==='begin')return {episode_id:1,profile:'pi-0.85.1-hosted-workspace-v1'};
 receipts.push(value);return {state:'capturing'};
})({on:(name,handler)=>handlers.set(name,handler),getActiveTools:()=>['lookup'],getAllTools:()=>[]});
await handlers.get('agent_start')({}, {sessionManager:{getSessionId:()=> 'synthetic-planner'}});
await handlers.get('context')({messages:[{role:'user',content:'Inspect project evidence.'}]});
await handlers.get('message_start')({message:{role:'assistant'}});
for(const delta of ['Visible ', 'Visible '])await handlers.get('message_update')({assistantMessageEvent:{
 type:'text_delta',contentIndex:0,delta,partial:{content:[{type:'thinking',thinking:'PRIVATE_PARTIAL_SNAPSHOT'}]}}});
await handlers.get('message_update')({assistantMessageEvent:{type:'thinking_delta',contentIndex:1,delta:'PRIVATE_THINKING_DELTA'}});
await handlers.get('message_update')({assistantMessageEvent:{type:'toolcall_delta',contentIndex:2,delta:'{"query":"evidence"}'}});
await handlers.get('message_end')({message:{role:'assistant',content:[
 {type:'thinking',thinking:'PRIVATE_FINAL_THINKING'}, {type:'text',text:'Visible Visible '}]}});
const call={toolCallId:'actual-lookup',toolName:'lookup',input:{query:'evidence'}};
await handlers.get('tool_call')(call);
await handlers.get('tool_execution_start')({...call,args:call.input});
await handlers.get('tool_execution_update')({...call,args:call.input,partialResult:{content:[{type:'text',text:'First row'}],details:{rows:1}}});
await handlers.get('tool_execution_end')({...call,result:{content:[{type:'text',text:'Lookup failed after first row'}],details:{rows:1}},isError:true});
await handlers.get('tool_result')({...call,content:[{type:'text',text:'Lookup failed after first row'}],isError:true});
await handlers.get('message_start')({message:{role:'assistant'}});
await handlers.get('message_update')({assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:'Unfinished public reply'}});
console.log(JSON.stringify(receipts));
'''.replace("__EXTENSION__", json.dumps(Path(runtime.EXTENSION).as_uri()))
    process = subprocess.run(["node", "--input-type=module"], input=script, text=True,
                             capture_output=True, check=True, timeout=10)
    assert "PRIVATE_" not in process.stdout
    receipts = json.loads(process.stdout)
    assert all("partial" not in receipt["payload"] for receipt in receipts if receipt["kind"] == "message_delta")
    episode = training.capture.begin(
        "tenant-one", "s-project", "synthetic-planner", observer_verified=True, allowed_tools=["lookup"],
        runtime_profile=HOSTED_PROFILE,
        runtime_metadata={"capture_policy": "retain-observed-v2", "run_label": "planner.cycle0", "mission_id": None},
    )["episode_id"]
    for receipt in receipts:
        training.capture.event("tenant-one", "s-project", episode, receipt["kind"], receipt["payload"])
    training.capture.event("tenant-one", "s-project", episode, "quarantine", {"reason": "runtime_call_unsettled"})
    with training.analytics._db() as db:
        observed = training.capture.events(db, episode)
        assert db.execute("SELECT state FROM training_tool_episodes WHERE id=?", (episode,)).fetchone()[0] == "interrupted"
    deltas = [event["payload"] for event in observed if event["kind"] == "message_delta"]
    assert [item["delta"] for item in deltas if item["type"] == "text_delta"] == [
        "Visible ", "Visible ", "Unfinished public reply",
    ]
    assert [event["kind"] for event in observed if event["kind"].startswith("tool_execution_")] == [
        "tool_execution_start", "tool_execution_update", "tool_execution_end",
    ]
    ending = next(event["payload"] for event in observed if event["kind"] == "tool_execution_end")
    assert ending["isError"] is True and ending["result"]["details"] == {"rows": 1}
    assert observed[-2]["kind"] == "message_delta" and observed[-1]["kind"] == "quarantine"
    assert "PRIVATE_" not in json.dumps(observed)
