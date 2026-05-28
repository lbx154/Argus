from __future__ import annotations

from .core.state_store import LoopStateStore, OperatorMessage


class LoopControlState(LoopStateStore):
    def __init__(self, operator_messages_file: str | None = None) -> None:
        super().__init__(objective="", operator_messages_file=operator_messages_file)


__all__ = ["LoopControlState", "OperatorMessage"]
