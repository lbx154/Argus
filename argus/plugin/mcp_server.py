"""Shared stdio MCP transport for the Argus host plugin."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .service import ArgusPluginService

mcp = FastMCP("argus", json_response=True, log_level="ERROR")
_SERVICE: ArgusPluginService | None = None


def _service() -> ArgusPluginService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ArgusPluginService()
    return _SERVICE


@mcp.tool()
def argus_project_create(workdir: str, name: str = "") -> dict[str, Any]:
    """Create an idle Argus project bound to an existing work directory."""
    return _service().create_project(workdir, name=name)


@mcp.tool()
def argus_project_list(workdir: str = "") -> dict[str, Any]:
    """List Argus projects, optionally filtered by exact work directory."""
    return _service().list_projects(workdir)


@mcp.tool()
def argus_message(project_id: str, text: str) -> dict[str, Any]:
    """Send one operator turn through the Argus Manager front door."""
    return _service().message(project_id, text)


@mcp.tool()
def argus_status(project_id: str) -> dict[str, Any]:
    """Read bounded persisted status for one Argus project."""
    return _service().status(project_id)


@mcp.tool()
def argus_doctor(project_id: str) -> dict[str, Any]:
    """Run non-mutating Argus runtime and backend diagnostics."""
    return _service().doctor(project_id)


@mcp.tool()
def argus_stop(project_id: str, force: bool = False) -> dict[str, Any]:
    """Stop an Argus project, draining gracefully unless force is explicit."""
    return _service().stop(project_id, force=force)


@mcp.tool()
def argus_artifacts(project_id: str) -> dict[str, Any]:
    """List existing allowlisted artifacts selected by Argus."""
    return _service().artifacts(project_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
