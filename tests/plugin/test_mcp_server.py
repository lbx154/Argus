from __future__ import annotations

import asyncio
import json
from typing import Any

from argus.plugin import mcp_server


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def create_project(self, workdir: str, *, name: str = "") -> dict[str, Any]:
        self.calls.append(("create_project", workdir, name))
        return {"ok": True, "op": "create"}

    def list_projects(self, workdir: str = "") -> dict[str, Any]:
        self.calls.append(("list_projects", workdir))
        return {"ok": True, "op": "list"}

    def message(self, project_id: str, text: str) -> dict[str, Any]:
        self.calls.append(("message", project_id, text))
        return {"ok": True, "op": "message"}

    def status(self, project_id: str) -> dict[str, Any]:
        self.calls.append(("status", project_id))
        return {"ok": True, "op": "status"}

    def doctor(self, project_id: str) -> dict[str, Any]:
        self.calls.append(("doctor", project_id))
        return {"ok": True, "op": "doctor"}

    def stop(self, project_id: str, *, force: bool = False) -> dict[str, Any]:
        self.calls.append(("stop", project_id, force))
        return {"ok": True, "op": "stop"}

    def artifacts(self, project_id: str) -> dict[str, Any]:
        self.calls.append(("artifacts", project_id))
        return {"ok": True, "op": "artifacts"}


def test_mcp_tools_delegate_to_one_service(monkeypatch) -> None:
    fake = FakeService()
    monkeypatch.setattr(mcp_server, "_SERVICE", fake)

    results = [
        mcp_server.argus_project_create("/tmp/work", "demo"),
        mcp_server.argus_project_list("/tmp/work"),
        mcp_server.argus_message("s1", "do work"),
        mcp_server.argus_status("s1"),
        mcp_server.argus_doctor("s1"),
        mcp_server.argus_stop("s1", force=True),
        mcp_server.argus_artifacts("s1"),
    ]

    assert [row["op"] for row in results] == [
        "create",
        "list",
        "message",
        "status",
        "doctor",
        "stop",
        "artifacts",
    ]
    assert fake.calls == [
        ("create_project", "/tmp/work", "demo"),
        ("list_projects", "/tmp/work"),
        ("message", "s1", "do work"),
        ("status", "s1"),
        ("doctor", "s1"),
        ("stop", "s1", True),
        ("artifacts", "s1"),
    ]
    json.dumps(results)


def test_mcp_registers_exact_public_tool_surface() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())

    assert mcp_server.mcp.settings.log_level == "ERROR"
    assert {tool.name for tool in tools} == {
        "argus_project_create",
        "argus_project_list",
        "argus_message",
        "argus_status",
        "argus_doctor",
        "argus_stop",
        "argus_artifacts",
    }
    for tool in tools:
        assert tool.description
        assert tool.inputSchema["type"] == "object"


def test_main_runs_stdio_transport(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kwargs: calls.append(kwargs))

    mcp_server.main()

    assert calls == [{"transport": "stdio"}]
