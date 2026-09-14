"""The advisor's narrow stdio MCP surface, including request cancellation."""
from __future__ import annotations

import json
import os
import uuid
from functools import partial

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .transport import PORT_ENV, TIMEOUT_ENV, TOKEN_ENV, request


def run_server() -> None:
    server = FastMCP("argus_advisor", json_response=True, log_level="ERROR")
    environment = {name: os.environ[name] for name in (PORT_ENV, TOKEN_ENV, TIMEOUT_ENV) if name in os.environ}
    instance = uuid.uuid4().hex

    @server.tool()
    async def consult_advisor(question: str, evidence_refs: list[str], ctx: Context) -> dict:
        """Request independent advice about explicit project evidence; the host owns scope and budget."""
        request_id = f"mcp:{instance}:{ctx.request_id}"
        try:
            result = await anyio.to_thread.run_sync(partial(
                request, "consult", {"question": question, "evidence_refs": evidence_refs, "request_id": request_id},
                env=environment,
            ), abandon_on_cancel=True)
        except BaseException:
            # MCP cancellation must also stop the independent provider. Shield
            # only this bounded cleanup; never keep the cancelled tool waiting.
            with anyio.CancelScope(shield=True), anyio.move_on_after(1.5):
                try:
                    await anyio.to_thread.run_sync(partial(
                        request, "cancel", {"request_id": request_id},
                        env={**environment, TIMEOUT_ENV: "1"},
                    ), abandon_on_cancel=True)
                except (OSError, ValueError):
                    pass
            raise
        if result.get("status") != "completed":
            raise ToolError(json.dumps(result, ensure_ascii=False))
        return result

    server.run(transport="stdio")


__all__ = ["run_server"]
