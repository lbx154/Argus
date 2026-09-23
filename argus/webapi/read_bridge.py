"""One bounded read query over stdin/stdout for the TypeScript API.

The existing Python stores retain their locking and recovery semantics. This
bridge exposes no command dispatcher, dynamic module/function name or write API.
Costs stream normalized records followed by a terminal receipt; other methods
return a single receipt. The Node process folds the cost records.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.contract_resources import contract_schema_path
from .cost_inputs import CostInputLimitError

CONTRACT = json.loads(contract_schema_path("read_api_protocol.json").read_text(encoding="utf-8"))


class InvalidQuery(ValueError):
    pass


def _integer(params: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    value = params.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidQuery(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _boolean(params: dict, name: str, default: bool) -> bool:
    value = params.get(name, default)
    if type(value) is not bool:
        raise InvalidQuery(f"{name} must be boolean")
    return value


def _project_id(value: Any) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or len(value) > CONTRACT["max_project_id_length"]
            or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InvalidQuery("invalid project id")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidQuery("invalid project id") from exc
    return value


def dispatch_query(
    method: str, params: dict, *, global_root: Path,
    emit_cost_frame: Callable[[dict], None] | None = None,
) -> Any:
    allowed = {
        "meta": set(), "projects": {"limit", "include_empty"},
        "costs": {"limit"}, "snapshot": {"sid", "events_limit", "compact"},
    }
    if method not in allowed or set(params) - allowed[method]:
        raise InvalidQuery("unsupported read operation or parameters")
    # Import after the process has bound ARGUS_SKILL_HOME to the explicit root.
    from .protocol import build_api_meta

    if method == "meta":
        return build_api_meta()
    from . import project_state
    if method in {"projects", "costs"}:
        limit = _integer(params, "limit", 100, 1, CONTRACT["max_projects"])
        if method == "projects":
            rows = project_state.list_projects(
                global_root=global_root, limit=limit,
                include_empty=_boolean(params, "include_empty", False),
            )
            return {"projects": rows, "local_cwd": ""}
        from .cost_inputs import cost_input_frames

        if emit_cost_frame is None:
            raise InvalidQuery("cost reads require a streaming receiver")
        projects = 0
        for frame in cost_input_frames(
            global_root, limit=limit, max_records=CONTRACT["max_cost_records"], validate_id=_project_id,
        ):
            emit_cost_frame(frame)
            projects += frame["kind"] == "cost_project_end"
        return {"project_count": projects, "generated_at": time.time()}
    sid = _project_id(params.get("sid"))
    return project_state.build_snapshot(
        sid, global_root=global_root,
        events_limit=_integer(params, "events_limit", 80, 0, CONTRACT["max_events"]),
        compact=_boolean(params, "compact", False),
    )


def _reject_constant(value: str) -> None:
    raise InvalidQuery("request must contain finite JSON values")


def reply(
    raw: bytes, *, global_root: Path, emit_cost_frame: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "protocol": CONTRACT["bridge_protocol"], "version": CONTRACT["bridge_version"], "id": None,
    }
    emitted_bytes = 0

    def emit(frame: dict) -> None:
        nonlocal emitted_bytes
        response = {**envelope, **frame}
        size = len(json.dumps(response, ensure_ascii=True, allow_nan=False).encode("utf-8")) + 1
        emitted_bytes += size
        if size > CONTRACT["max_cost_frame_bytes"] or emitted_bytes > CONTRACT["max_response_bytes"]:
            raise CostInputLimitError("cost input stream exceeds the response limit")
        if emit_cost_frame is None:
            raise InvalidQuery("cost reads require a streaming receiver")
        emit_cost_frame(response)

    try:
        if len(raw) > CONTRACT["max_request_bytes"]:
            raise InvalidQuery("read query exceeds the request limit")
        query = json.loads(raw, parse_constant=_reject_constant)
        if not isinstance(query, dict):
            raise InvalidQuery("read query must be an object")
        request_id = query.get("id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise InvalidQuery("invalid request id")
        envelope["id"] = request_id
        if (set(query) != {"protocol", "version", "id", "method", "params"}
                or query.get("protocol") != CONTRACT["bridge_protocol"]
                or type(query.get("version")) is not int
                or query["version"] != CONTRACT["bridge_version"]
                or not isinstance(query.get("method"), str)
                or not isinstance(query.get("params"), dict)):
            raise InvalidQuery("unsupported read query envelope")
        # Libraries may write diagnostics; stdout is reserved for protocol frames.
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch_query(
                query["method"], query["params"], global_root=global_root, emit_cost_frame=emit,
            )
        response = {**envelope, "ok": True, "result": result}
        encoded = json.dumps(response, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(encoded) + emitted_bytes + 1 > CONTRACT["max_response_bytes"]:
            return {**envelope, "ok": False, "error": {"code": "response_too_large", "detail": "read response exceeds the response limit"}}
        return response
    except CostInputLimitError:
        return {**envelope, "ok": False, "error": {"code": "response_too_large", "detail": "cost input stream exceeds the response limit"}}
    except (InvalidQuery, json.JSONDecodeError, UnicodeError) as exc:
        return {**envelope, "ok": False, "error": {"code": "invalid_query", "detail": str(exc)}}
    except Exception as exc:  # noqa: BLE001 - one failed read never becomes a successful receipt
        print(f"read bridge failed: {type(exc).__name__}", file=sys.stderr)
        return {**envelope, "ok": False, "error": {"code": "query_failed", "detail": "unable to read project state"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.global_root.expanduser().resolve()
    os.environ["ARGUS_SKILL_HOME"] = str(root)
    os.environ.pop("ARGUS_DESKTOP_LAUNCH_NONCE", None)
    raw = sys.stdin.buffer.read(CONTRACT["max_request_bytes"] + 1)
    # Capture the protocol channel before libraries' stdout is redirected.
    output = sys.stdout

    def emit(frame: dict) -> None:
        output.write(json.dumps(frame, ensure_ascii=True, allow_nan=False) + "\n")
        output.flush()

    result = reply(raw, global_root=root, emit_cost_frame=emit)
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
