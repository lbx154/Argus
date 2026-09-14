"""Per-execution cancellation identity shared by prompt reads and backends."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterator

from ..core.run_gateway import current_run_interrupt_provider, run_interrupt_scope

_EXECUTION_INTERRUPT: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "argus_runtime_execution_interrupt", default=None,
)


def current_execution_interrupt_provider() -> Callable[[], str | None] | None:
    """Let a backend default use the same retained, frozen execution identity."""
    return _EXECUTION_INTERRUPT.get()


@contextmanager
def execution_interrupt_scope(
    *, stop_event: Any, state_root: Path | None, mission_id: str, enable_abort: bool,
) -> Iterator[None]:
    # Copy identity values now: the cached runner is reused for later missions,
    # while an abandoned embedding worker can retain this copied context.
    root = Path(state_root) if state_root is not None else None
    target = str(mission_id or "")
    active = Event()
    active.set()

    def reason() -> str | None:
        if not active.is_set():
            return "mission execution scope ended"
        if stop_event is not None and stop_event.is_set():
            return "daemon stop requested"
        if enable_abort and stop_event is not None and root is not None and target:
            from ..life.memory import consume_running_item_abort

            aborted = consume_running_item_abort(root, target_item_id=target)
            if aborted:
                return "operator abort requested: " + aborted
        return None

    with run_interrupt_scope(reason, retain_first_reason=True):
        provider = current_run_interrupt_provider()
        assert provider is not None
        token = _EXECUTION_INTERRUPT.set(provider)
        try:
            yield
        finally:
            active.clear()
            _EXECUTION_INTERRUPT.reset(token)
