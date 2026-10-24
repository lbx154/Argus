from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CancelledNotification, CancelledNotificationParams, ClientNotification

from argus.advisor import runtime
from argus.advisor.config import save_advisor_config
from argus.advisor.transport import TOKEN_ENV
from argus.core.models import RunnerOptions


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
    if os.name != "nt":  # Windows protects files with ACLs, not POSIX mode bits.
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
        command=server["command"], args=server["args"],
        # Windows children need SystemRoot to initialize sockets and crypto.
        env={**{name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ},
             "PATH": os.defpath, **server["env"]},
    )


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
