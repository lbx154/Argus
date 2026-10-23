"""Bind one call's Argus actions to the agent CLI that runs it.

The actions are served over a call-bound bridge (``core.role_tool_bridge``)
and reach the model as native tools: a Pi extension, or an MCP stdio server
for Codex, Copilot, Claude and Qoder. A backend that can do neither cannot
run a role, and ``core.backend_readiness`` refuses it before any call.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Iterator

from .role_tool_bridge import CallBoundBridge, bridge_request

if TYPE_CHECKING:
    from .models import RunnerOptions

PREFIX = "ARGUS_PLUGIN_ACTION"
SERVER = "argus_actions"
#: The agent CLIs that can submit Argus actions.
ACTION_BACKENDS = frozenset({"pi", "copilot", "codex", "claude", "qoder"})


@contextmanager
def role_actions(runner: Any, options: "RunnerOptions", actions: Any) -> Iterator["RunnerOptions"]:
    """Serve ``actions.dispatch`` for one call; yields the call's options with its tools bound."""
    backend = str(getattr(runner, "backend", "")).lower()
    if backend not in ACTION_BACKENDS | {"memory", ""}:
        raise ValueError(f"Backend {backend!r} cannot submit Argus actions.")
    with (
        CallBoundBridge(actions.dispatch, env_prefix=PREFIX) as bridge,
        TemporaryDirectory(prefix="argus-actions-") as directory,
    ):
        names = [tool["name"] for tool in actions.tools]
        environment = {**bridge.environment, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        server = {"command": sys.executable, "args": ["-P", "-m", __name__]}
        extra = list(options.extra_args or [])
        extensions = list(options.trusted_extensions or [])
        if backend == "pi":
            extensions.append(str(Path(__file__).with_name("pi_actions.mjs")))
        elif backend == "codex":
            for key, value in {
                **server, "env_vars": list(bridge.environment), "env.PYTHONPATH": environment["PYTHONPATH"],
                # Codex asks before an MCP call, and a call with no one to ask is refused.
                "default_tools_approval_mode": "approve",
            }.items():
                extra.extend(["-c", f"mcp_servers.{SERVER}.{key}={json.dumps(value)}"])
        elif backend in {"copilot", "claude", "qoder"}:
            entry = {**server, "env": environment}
            if backend == "copilot":
                entry.update(type="local", tools=names)
            path = Path(directory) / "mcp.json"
            descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0) | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"mcpServers": {SERVER: entry}}, handle)
            extra.extend(
                ["--additional-mcp-config", "@" + str(path)] if backend == "copilot"
                else ["--mcp-config", str(path)]
            )
            names = [f"{SERVER}-{name}" if backend == "copilot" else f"mcp__{SERVER}__{name}" for name in names]
        yield replace(
            options, extra_args=extra, trusted_extensions=extensions,
            trusted_tool_names=[*(options.trusted_tool_names or []), *names],
            extension_env={**(options.extension_env or {}), **bridge.environment},
        )


def main() -> None:
    """The MCP stdio server: lists the bridge's tools and forwards each call to it."""
    import asyncio

    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server(SERVER)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(**tool) for tool in bridge_request(PREFIX, "tools", {})["tools"]]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        result = bridge_request(PREFIX, name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result))]

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(serve())


if __name__ == "__main__":
    main()
