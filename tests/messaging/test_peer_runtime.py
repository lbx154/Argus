import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend, _core, _exec
from argus.core.models import RunnerOptions, RunnerResult
from argus.messaging import runtime
from argus.messaging.store import PeerMailbox
from argus.messaging.transport import request


def test_pi_manager_native_tool_reaches_durable_peer_queue_through_real_gateway(tmp_path, monkeypatch, platform_process_env):
    from argus.adapters.agent_cli_backend._exec_finalize import finalize_result
    from argus.core import secret_guard

    root = tmp_path / "tenant"
    state = root / "projects/project-a"
    state.mkdir(parents=True)
    (root / "projects/project-b").mkdir()
    monkeypatch.setattr(_core, "known_secret_values", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(secret_guard, "known_secret_values", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(_exec, "monitor_budget", lambda *_args: nullcontext())
    node = shutil.which("node")
    assert node
    environments, messages = [], []
    script = r'''
const {peerExtension}=await import(process.argv[1]);
const {bridgeRequest}=await import(process.argv[2]);
const Type={Object:properties=>({type:"object",properties}),String:()=>({type:"string"}),Array:items=>({type:"array",items})};
const tools={};
peerExtension((operation,payload,signal)=>bridgeRequest("ARGUS_PLUGIN_PEER",operation,payload,signal),Type)(
  {registerTool:tool=>{tools[tool.name]=tool;}}
);
const listed=await tools.list_peer_projects.execute("list",{});
if (!listed.details.projects.some(p=>p.project_id==="project-b")) throw Error("peer not discoverable");
const sent=await tools.send_peer_message.execute("native-1",{recipient:"project-b",text:"Which result did you verify?",evidence_refs:[]});
if (sent.isError) throw Error(JSON.stringify(sent));
const inspected=await tools.peer_message_status.execute("inspect",{message_id:sent.details.message.message_id});
process.stdout.write(JSON.stringify(inspected.details));
'''

    def provider(ctx, _options):
        assert "send_peer_message" in ctx.options.trusted_tool_names
        assert ctx.options.sandbox_mode == "read-only" and not ctx.options.dangerous_yolo
        command = ctx.backend._runner._build_pi_command(resume_thread_id=None, options=ctx.options)
        names = command[command.index("--tools") + 1].split(",")
        assert "send_peer_message" in names and "bash" not in names and "write" not in names
        environments.append(dict(ctx.options.extension_env))
        process = subprocess.run(
            [node, "--input-type=module", "-e", script, Path(runtime.EXTENSION).with_name("pi_tools.mjs").as_uri(),
             Path(runtime.__file__).parents[1].joinpath("core/role_tool_bridge.mjs").as_uri()],
            env={**platform_process_env, "PATH": os.defpath, **ctx.options.extension_env}, text=True, capture_output=True, timeout=10,
        )
        assert process.returncode == 0, process.stderr
        status = json.loads(process.stdout)
        assert status["message"]["sender"] == "project-a"
        assert status["message"]["parent_call_id"] == ctx.call_id
        assert status["message"]["authority"] == "peer_advisory"
        messages.append(status["message"])
        return finalize_result(ctx, RunnerResult(exit_code=0, agent_messages=["I asked the peer for evidence."]), status="completed")

    monkeypatch.setattr(_exec, "spawn_and_finish", provider)
    backend = AgentCliBackend(backend="pi", runner_bin="unused-fake-pi")
    backend.set_usage_context(project_root=state, global_root=root, mission_id="mission-a")
    options = RunnerOptions(working_dir=str(state), sandbox_mode="read-only", model="provider/fake-manager")
    result = backend.run_exec(prompt="Coordinate the verified results.", options=options, run_label="manager-chat")
    assert result.exit_code == 0
    assert options.extension_env is None
    assert PeerMailbox(root, "project-b").pending()[0]["message_id"] == messages[0]["message_id"]
    with pytest.raises(OSError):
        request("status", {"message_id": messages[0]["message_id"]}, env=environments[0])


@pytest.mark.parametrize("label", ["engineer-r1", "planner.cycle1", "reviewer", "peer-message-response"])
def test_peer_tool_is_not_available_to_execution_roles_or_automatic_replies(tmp_path, label):
    options = RunnerOptions()
    ctx = SimpleNamespace(options=options, run_label=label, usage_project_root=tmp_path)
    with runtime.peer_run(ctx):
        assert ctx.options is options
