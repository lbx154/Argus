from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CancelledNotification, CancelledNotificationParams, ClientNotification

from argus_skill.adapters.agent_cli_backend import AgentCliBackend, _exec
from argus_skill.advisor import runtime
from argus_skill.advisor.config import save_advisor_config
from argus_skill.advisor.copilot import TOOL_NAME
from argus_skill.advisor.transport import TOKEN_ENV, request
from argus_skill.core.models import RunnerOptions, RunnerResult


class RecordingService:
    def __init__(self, context, config, *, blocked=False):
        self.context, self.config = context, config
        self.calls = []
        self.cancelled = []
        self.entered, self.release, self.closed = (threading.Event() for _ in range(3))
        if not blocked:
            self.release.set()

    @staticmethod
    def _redact(text):
        return text

    def consult(self, question, refs, request_id):
        self.calls.append((question, refs, request_id))
        self.entered.set()
        assert self.release.wait(5)
        return {"status": "cancelled" if request_id in self.cancelled else "completed",
                "consultation_id": "fixture-advice", "answer": "Verify the failure path",
                "parent_call_id": self.context.parent_call_id, "caller_role": self.context.caller_role}

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        self.release.set()

    def close(self):
        self.closed.set()
        self.release.set()


def mcp_parameters(command):
    path = Path(command[command.index("--additional-mcp-config") + 1][1:])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    config = json.loads(path.read_text())["mcpServers"]
    assert set(config) == {"argus_advisor"}
    server = config["argus_advisor"]
    assert server["tools"] == ["consult_advisor"]
    assert set(server["env"]) == {
        "PYTHONPATH", "ARGUS_PLUGIN_ADVISOR_PORT", "ARGUS_PLUGIN_ADVISOR_TOKEN", "ARGUS_PLUGIN_ADVISOR_TIMEOUT",
    }
    assert server["env"][TOKEN_ENV] not in " ".join(command)
    return path, StdioServerParameters(
        command=server["command"], args=server["args"], env={"PATH": os.defpath, **server["env"]},
    )


@pytest.mark.parametrize("role", ["manager", "planner", "engineer", "reviewer"])
def test_readonly_copilot_gateway_loads_only_bound_advisor_over_stdio(tmp_path, monkeypatch, role):
    """Use actual admission/options/CLI construction and MCP transport, with no provider inference."""
    from argus_skill.adapters.agent_cli_backend._exec_finalize import finalize_result

    state, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    save_advisor_config(state, {"enabled": True, "backend": "pi", "model": "test/expert"})
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "off")
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "0")
    monkeypatch.setenv(TOKEN_ENV, "unrelated-parent-capability")
    monkeypatch.setattr(_exec, "monitor_budget", lambda *_args: nullcontext())
    services, paths, environments = [], [], []

    def create_service(context, config):
        service = RecordingService(context, config)
        services.append(service)
        return service

    monkeypatch.setattr(runtime, "AdvisorService", create_service)

    async def call_tool(params):
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert [tool.name for tool in tools] == ["consult_advisor"]
            assert set(tools[0].inputSchema["properties"]) == {"question", "evidence_refs"}
            result = await session.call_tool("consult_advisor", {
                "question": "Which failure path needs verification?", "evidence_refs": ["evidence.txt"],
            })
            assert not result.isError
            return json.loads(result.content[0].text)

    def provider(ctx, cli_options):
        assert len(services) == 1
        runner = ctx.backend._runner
        options = runner._apply_sandbox_policy(cli_options)
        assert options.sandbox_mode == "read-only" and options.force_safe_mode
        assert not runner._acp_enabled(ctx.run_label, options=options)
        command = runner._build_command(resume_thread_id=None, options=options)
        available = command[command.index("--available-tools") + 1].split(",")
        assert available == ["view", "rg", "glob", TOOL_NAME]
        assert "argus_advisor(consult_advisor)" in command
        assert "--yolo" not in command and "--allow-all-tools" not in command
        path, params = mcp_parameters(command)
        paths.append(path)
        environments.append(dict(ctx.options.extension_env))
        assert params.env[TOKEN_ENV] != os.environ[TOKEN_ENV]
        assert params.env[TOKEN_ENV] not in ctx.prompt
        assert TOOL_NAME in ctx.prompt and "python -m argus_skill.tools.advisor consult" not in ctx.prompt
        result = asyncio.run(call_tool(params))
        assert result["parent_call_id"] == ctx.call_id and result["caller_role"] == role
        assert services[0].context.mission_id == "bound-mission"
        return finalize_result(ctx, RunnerResult(exit_code=0, agent_messages=["Advice received"], usage_model="test-main"), status="completed")

    monkeypatch.setattr(_exec, "spawn_and_finish", provider)
    backend = AgentCliBackend(backend="copilot", runner_bin="unused-fake-copilot")
    backend.set_usage_context(project_root=state, global_root=tmp_path / "global", mission_id="bound-mission")
    original = RunnerOptions(model="test-main", working_dir=str(workspace), sandbox_mode="read-only",
                             extra_args=["--available-tools", "shell,write", "--allow-all-tools"])
    result = backend.run_exec(prompt="Inspect current evidence", options=original, run_label=f"{role}.turn")
    assert result.exit_code == 0
    assert len(services[0].calls) == 1 and services[0].closed.is_set()
    assert not paths[0].exists() and not paths[0].parent.exists()
    assert original.trusted_tool_names is None and original.extension_env is None
    assert original.extra_args == ["--available-tools", "shell,write", "--allow-all-tools"]
    assert os.environ[TOKEN_ENV] == "unrelated-parent-capability"
    with pytest.raises(OSError):
        request("receipt", {"consultation_id": "fixture-advice"}, env=environments[0])


def test_mcp_cancellation_reaches_the_same_host_request_and_closes_resources(tmp_path, monkeypatch):
    save_advisor_config(tmp_path, {"enabled": True, "backend": "pi", "model": "test/expert"})
    services = []

    def create_service(context, config):
        services.append(RecordingService(context, config, blocked=True))
        return services[-1]

    monkeypatch.setattr(runtime, "AdvisorService", create_service)
    ctx = SimpleNamespace(options=RunnerOptions(working_dir=str(tmp_path), sandbox_mode="read-only"),
                          run_label="reviewer.turn", usage_project_root=tmp_path, call_id="parent-cancel",
                          usage_mission_id="mission", usage_global_root=tmp_path,
                          backend=SimpleNamespace(_backend_name="copilot"), prompt="Read evidence")

    async def cancel_tool(params):
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            pending = asyncio.create_task(session.call_tool("consult_advisor", {"question": "Wait", "evidence_refs": []}))
            assert await asyncio.to_thread(services[0].entered.wait, 2)
            request_id = int(services[0].calls[0][2].rsplit(":", 1)[1])
            await session.send_notification(ClientNotification(CancelledNotification(
                params=CancelledNotificationParams(requestId=request_id, reason="Caller cancelled"),
            )))
            assert await asyncio.to_thread(services[0].release.wait, 2)
            assert services[0].cancelled == [services[0].calls[0][2]]
            # Consume the server's cancellation response before closing stdio.
            # Cancelling this local waiter instead races the SDK stdout reader
            # against ClientSession's closed receive stream and skips checking
            # whether the actual MCP caller received the terminal response.
            with pytest.raises(McpError, match="Request cancelled"):
                await asyncio.wait_for(pending, 2)
            await asyncio.wait_for(session.send_ping(), 2)

    with pytest.raises(RuntimeError, match="caller stopped"):
        with runtime.advisor_run(ctx):
            path, params = mcp_parameters(ctx.options.extra_args)
            asyncio.run(cancel_tool(params))
            raise RuntimeError("caller stopped")
    assert services[0].closed.is_set() and not path.parent.exists()


@pytest.mark.parametrize("backend,disabled", [("codex", False), ("claude", False), ("opencode", False), ("copilot", True)])
def test_unsupported_readonly_or_disabled_calls_never_get_shell_fallback(tmp_path, backend, disabled):
    save_advisor_config(tmp_path, {"enabled": True, "backend": "pi", "model": "test/expert"})
    options = RunnerOptions(working_dir=str(tmp_path), sandbox_mode="read-only", disable_tools=disabled)
    ctx = SimpleNamespace(options=options, run_label="reviewer.turn", usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name=backend), prompt="Original")
    with runtime.advisor_run(ctx):
        assert ctx.options is options and ctx.prompt == "Original"
