"""Keep peer consumption out of the operator inbox's string interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .handler import process_peer_messages
from .store import mailbox_for_project


class PeerAwareInbox:
    def __init__(self, project_root: Path, operator_drain: Callable[[], str | None]) -> None:
        self.project_root = Path(project_root)
        self.operator_drain = operator_drain

    def __call__(self) -> str | None:
        return self.operator_drain()

    def drain_peer(self, manager: Any, *, cancelled: Callable[[], bool] | None = None) -> int:
        if self.project_root.parent.name != "projects":
            return 0
        if not mailbox_for_project(self.project_root).pending():
            return 0
        manager = manager() if callable(manager) else manager
        return process_peer_messages(self.project_root, manager, cancelled=cancelled)
