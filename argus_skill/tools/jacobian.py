"""Process-isolated MCP bridge to Jacobian's typed mathematical operations.

Argus and Jacobian deliberately keep independent Python environments. Argus
uses its existing MCP client to start ``jacobian-mcp`` as a stdio sidecar, so it
does not import Jacobian or resolve Jacobian's pinned backend dependencies in
the Argus process. The bridge exposes Jacobian's own ``math.find`` and
``math.run`` contracts without inventing a second search or result schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

JACOBIAN_MCP_BIN_ENV = "ARGUS_SKILL_JACOBIAN_MCP_BIN"
DEFAULT_RUN_TIMEOUT_SECONDS = 900
MAX_RUN_TIMEOUT_SECONDS = 3600
MAX_REQUEST_BYTES = 128 * 1024
_OPERATION_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789_.-"
)


class JacobianAdapterError(RuntimeError):
    """The sidecar was absent, incompatible, or unavailable."""


class JacobianMcpError(JacobianAdapterError):
    """A structured error returned by Jacobian's MCP contract."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        super().__init__(str(self.payload.get("message") or "Jacobian MCP error"))


McpCaller = Callable[[str | None, dict[str, Any], int, Path], dict[str, Any]]


def _safe_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Pass only process essentials; never forward model/API credentials."""
    incoming = os.environ if source is None else source
    allowed = (
        "HOME",
        "USERPROFILE",
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
    )
    env = {key: incoming[key] for key in allowed if incoming.get(key)}
    env.update(
        {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def resolve_jacobian_mcp_executable(
    env: Mapping[str, str] | None = None,
) -> Path | None:
    source = os.environ if env is None else env
    if env is None:
        from ..core.knobs import resolve_knob

        requested = resolve_knob(JACOBIAN_MCP_BIN_ENV, "", env=source).value.strip()
    else:
        requested = str(source.get(JACOBIAN_MCP_BIN_ENV) or "").strip()
    candidate = shutil.which(requested or "jacobian-mcp")
    if candidate is None and requested:
        candidate = str(Path(requested).expanduser())
    if not candidate:
        return None
    path = Path(candidate).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def jacobian_capability_note() -> str:
    executable = resolve_jacobian_mcp_executable()
    if executable is None:
        return ""
    source_interpreter = (
        Path(__file__).resolve().parents[2]
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    interpreter = source_interpreter if source_interpreter.is_file() else Path(sys.executable)
    module = f"{interpreter} -m argus_skill.tools.jacobian"
    return (
        "\n\n## This host's Jacobian capability\n\n"
        f"Jacobian MCP is available at `{executable}` through Argus's isolated "
        "sidecar bridge. Use Jacobian's own bounded typed contracts:\n"
        f"- discover: `{module} find --query \"exact outcome\"`\n"
        f"- browse: `{module} browse --domain <domain>`\n"
        f"- inspect: `{module} inspect --operation <operation_id>`\n"
        f"- execute: `{module} run --operation <operation_id> --payload-file <json>`\n"
        "Inspect before an unfamiliar run. Preserve the server version, protocol, "
        "operation id, exact payload, typed output, and structured error. Timeout, "
        "transport failure, incomplete output, and UNKNOWN are non-conclusions."
    )


def _structured_result(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for item in getattr(result, "content", ()) or ():
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def _find_mcp_error(exc: BaseException) -> dict[str, Any] | None:
    if isinstance(exc, McpError):
        return exc.error.model_dump(mode="json")
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            found = _find_mcp_error(child)
            if found is not None:
                return found
    return None


async def _call_mcp_async(
    tool_name: str | None,
    arguments: dict[str, Any],
    timeout: int,
    executable: Path,
) -> dict[str, Any]:
    server = StdioServerParameters(
        command=str(executable),
        env=_safe_env(),
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with Path(os.devnull).open("w", encoding="utf-8") as errlog:
        async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                tool_names = sorted(tool.name for tool in listed.tools)
                server_info = initialized.serverInfo.model_dump(mode="json")
                base = {
                    "schema_version": 1,
                    "transport": "mcp-stdio",
                    "protocol_version": initialized.protocolVersion,
                    "server": server_info,
                    "tools": tool_names,
                }
                if tool_name is None:
                    return base
                if tool_name not in tool_names:
                    raise JacobianAdapterError(
                        f"Jacobian sidecar does not expose required tool {tool_name!r}"
                    )
                result = await session.call_tool(
                    tool_name,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=timeout),
                )
                if result.isError:
                    raise JacobianMcpError(
                        {
                            "code": "TOOL_ERROR",
                            "message": "Jacobian tool returned an error result",
                            "content": [
                                item.model_dump(mode="json") for item in result.content
                            ],
                        }
                    )
                structured = _structured_result(result)
                if structured is None:
                    raise JacobianAdapterError(
                        "Jacobian tool returned no structured mathematical result"
                    )
                return {
                    **base,
                    "tool": tool_name,
                    "request": arguments,
                    "result": structured,
                }


def _call_mcp(
    tool_name: str | None,
    arguments: dict[str, Any],
    timeout: int,
    executable: Path,
) -> dict[str, Any]:
    try:
        return asyncio.run(
            _call_mcp_async(tool_name, arguments, timeout, executable)
        )
    except JacobianAdapterError:
        raise
    except BaseException as exc:
        structured = _find_mcp_error(exc)
        if structured is not None:
            raise JacobianMcpError(structured) from exc
        raise JacobianAdapterError(
            f"Jacobian MCP sidecar failed: {str(exc) or type(exc).__name__}"
        ) from exc


def _executable(value: Path | None) -> Path:
    executable = value or resolve_jacobian_mcp_executable()
    if executable is None:
        raise JacobianAdapterError(
            f"Jacobian MCP is unavailable; set {JACOBIAN_MCP_BIN_ENV}"
        )
    return executable


def _operation_id(value: str) -> str:
    operation_id = str(value or "").strip()
    if (
        not operation_id
        or len(operation_id) > 200
        or operation_id[0] not in "abcdefghijklmnopqrstuvwxyz0123456789"
        or any(char not in _OPERATION_ID_CHARS for char in operation_id)
    ):
        raise JacobianAdapterError("invalid Jacobian operation id")
    return operation_id


def status(
    *,
    executable: Path | None = None,
    caller: McpCaller = _call_mcp,
) -> dict[str, Any]:
    return caller(None, {}, 120, _executable(executable))


def find_operations(
    query: str,
    *,
    domain: str | None = None,
    limit: int = 5,
    cursor: str | None = None,
    executable: Path | None = None,
    caller: McpCaller = _call_mcp,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "op": "search",
        "query": str(query or "").strip(),
        "limit": max(1, min(int(limit), 20)),
    }
    if not request["query"]:
        raise JacobianAdapterError("find query must not be empty")
    if domain:
        request["domain"] = domain
    if cursor:
        request["cursor"] = cursor
    return caller(
        "math.find",
        {"request": request},
        120,
        _executable(executable),
    )


def browse_operations(
    *,
    domain: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
    executable: Path | None = None,
    caller: McpCaller = _call_mcp,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "op": "browse",
        "limit": max(1, min(int(limit), 20)),
    }
    if domain:
        request["domain"] = domain
    if cursor:
        request["cursor"] = cursor
    return caller(
        "math.find",
        {"request": request},
        120,
        _executable(executable),
    )


def inspect_operation(
    operation_id: str,
    *,
    executable: Path | None = None,
    caller: McpCaller = _call_mcp,
) -> dict[str, Any]:
    return caller(
        "math.find",
        {"request": {"op": "inspect", "operation_id": _operation_id(operation_id)}},
        120,
        _executable(executable),
    )


def run_operation(
    operation_id: str,
    payload: Mapping[str, Any],
    *,
    timeout: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    executable: Path | None = None,
    caller: McpCaller = _call_mcp,
) -> dict[str, Any]:
    if not 1 <= int(timeout) <= MAX_RUN_TIMEOUT_SECONDS:
        raise JacobianAdapterError(
            f"timeout must be between 1 and {MAX_RUN_TIMEOUT_SECONDS} seconds"
        )
    request = {
        "operation_id": _operation_id(operation_id),
        "payload": dict(payload),
    }
    size = len(
        json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    if size > MAX_REQUEST_BYTES:
        raise JacobianAdapterError(
            f"request exceeds the {MAX_REQUEST_BYTES}-byte adapter limit"
        )
    return caller(
        "math.run",
        request,
        int(timeout),
        _executable(executable),
    )


def _payload_file(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if path.suffix.casefold() != ".json":
        raise JacobianAdapterError(
            "payload file must use a dedicated .json path; never use a README, "
            "CSV, proof, or source file as temporary Jacobian input"
        )
    try:
        stat = path.lstat()
    except OSError as exc:
        raise JacobianAdapterError(f"cannot read payload file: {exc}") from exc
    if path.is_symlink() or not path.is_file() or stat.st_size > MAX_REQUEST_BYTES:
        raise JacobianAdapterError(
            "payload file must be a regular non-symlink file within the adapter limit"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JacobianAdapterError(f"payload file is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise JacobianAdapterError("payload must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use Jacobian math.find and math.run through an MCP sidecar."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report MCP protocol, server version, and tools")
    find = sub.add_parser("find", help="search Jacobian's installed operations")
    find.add_argument("--query", required=True)
    find.add_argument("--domain")
    find.add_argument("--limit", type=int, default=5)
    find.add_argument("--cursor")
    browse = sub.add_parser("browse", help="browse operation cards in ID order")
    browse.add_argument("--domain")
    browse.add_argument("--limit", type=int, default=20)
    browse.add_argument("--cursor")
    inspect = sub.add_parser("inspect", help="inspect one exact operation contract")
    inspect.add_argument("--operation", required=True)
    run = sub.add_parser("run", help="execute one typed mathematical operation")
    run.add_argument("--operation", required=True)
    run.add_argument("--payload-file", required=True)
    run.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            output = status()
        elif args.command == "find":
            output = find_operations(
                args.query,
                domain=args.domain,
                limit=args.limit,
                cursor=args.cursor,
            )
        elif args.command == "browse":
            output = browse_operations(
                domain=args.domain,
                limit=args.limit,
                cursor=args.cursor,
            )
        elif args.command == "inspect":
            output = inspect_operation(args.operation)
        else:
            output = run_operation(
                args.operation,
                _payload_file(args.payload_file),
                timeout=args.timeout,
            )
    except JacobianMcpError as exc:
        print(json.dumps({"ok": False, "error": exc.payload}, ensure_ascii=False))
        return 2
    except (JacobianAdapterError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
