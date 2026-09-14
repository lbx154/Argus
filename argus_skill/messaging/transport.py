"""Manager's host-bound send/read interface; no sender/root tool arguments."""
from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from ..core.role_tool_bridge import CallBoundBridge, bridge_request, require_fields
from .store import PeerMailbox

PREFIX = "ARGUS_PLUGIN_PEER"


class PeerToolService:
    def __init__(self, mailbox: PeerMailbox, *, parent_call_id: str, mission_id: str | None = None,
                 redact: Callable[[str], str] | None = None) -> None:
        self.mailbox, self.parent_call_id, self.mission_id = mailbox, parent_call_id, mission_id
        self.redact = redact or (lambda text: text)
        self.sent: set[str] = set()
        self._lock = threading.Lock()

    def dispatch(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        if operation == "projects":
            require_fields(data, set())
            return {"projects": self.mailbox.projects()}
        if operation == "status":
            require_fields(data, {"message_id"}, required={"message_id"})
            return self.mailbox.status(data["message_id"])
        if operation != "send":
            raise ValueError("unknown peer tool operation")
        require_fields(data, {"recipient", "text", "evidence_refs", "request_id"}, required={"recipient", "text"})
        request_id = data.get("request_id") or uuid.uuid4().hex
        if not isinstance(request_id, str):
            raise ValueError("invalid peer request id")
        if not isinstance(data["text"], str):
            raise ValueError("peer message text must be a string")
        refs = data.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("invalid peer evidence references")
        with self._lock:
            if request_id not in self.sent and len(self.sent) >= 4:
                raise ValueError("peer message limit reached for this Manager turn")
            message = self.mailbox.send(
                data["recipient"], self.redact(data["text"]), parent_call_id=self.parent_call_id,
                request_id=request_id, evidence_refs=[self.redact(ref) for ref in refs], mission_id=self.mission_id,
            )
            self.sent.add(request_id)
        return {"status": "queued", "message": message}


class PeerBridge(CallBoundBridge):
    def __init__(self, service: PeerToolService) -> None:
        super().__init__(service.dispatch, env_prefix=PREFIX, timeout_seconds=10, redact=service.redact)


def request(operation: str, payload: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    return bridge_request(PREFIX, operation, payload, env=env)
