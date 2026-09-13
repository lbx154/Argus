"""Advisor dispatch over the shared call-bound role-tool bridge."""
from __future__ import annotations

from typing import Any

from ..core.role_tool_bridge import CallBoundBridge, bridge_request, require_fields
from .receipts import read_receipt
from .service import AdvisorService

PREFIX = "ARGUS_PLUGIN_ADVISOR"
PORT_ENV = PREFIX + "_PORT"
TOKEN_ENV = PREFIX + "_TOKEN"
TIMEOUT_ENV = PREFIX + "_TIMEOUT"


def public_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "evidence"}


class AdvisorBridge(CallBoundBridge):
    def __init__(self, service: AdvisorService) -> None:
        self.service = service

        def dispatch(operation: str, data: dict[str, Any]) -> dict[str, Any]:
            if operation == "consult":
                require_fields(data, {"question", "evidence_refs", "request_id"})
                return public_receipt(service.consult(
                    data.get("question", ""), data.get("evidence_refs", []), data.get("request_id"),
                ))
            if operation == "cancel":
                require_fields(data, {"request_id"}, required={"request_id"})
                service.cancel(data["request_id"])
                return {"status": "cancel_requested"}
            if operation == "receipt":
                require_fields(data, {"consultation_id"}, required={"consultation_id"})
                receipt = read_receipt(service.context.project_root, data["consultation_id"])
                if receipt is None or receipt.get("parent_call_id") != service.context.parent_call_id:
                    raise ValueError("consultation does not belong to this role turn")
                return public_receipt(receipt)
            raise ValueError("unknown advisor operation")

        super().__init__(dispatch, env_prefix=PREFIX, timeout_seconds=service.config.timeout_seconds + 10,
                         on_close=service.close, redact=service._redact)


def request(operation: str, payload: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    return bridge_request(PREFIX, operation, payload, env=env)
