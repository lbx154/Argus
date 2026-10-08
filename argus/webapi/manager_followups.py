"""Messages the operator sends while a Manager chat turn is still running.

A chat reply runs inside the web server, so nothing in the project process
ever reads what was typed meanwhile. Each such message is shown in the
conversation at once and then handled as the next ordinary Manager turn, in
the order it was typed, once the running turn releases the conversation. The
Manager's front door makes the call for it exactly as for any other message:
with a live mission it can steer that mission, otherwise it answers in chat.
"""

from __future__ import annotations

import collections
import contextvars
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Operator message ids already shown in the conversation; the turn that later
# handles the message must not show it a second time.
PRESHOWN_OPERATOR_IDS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "argus_preshown_operator_ids", default=frozenset(),
)

_REGISTRY = threading.Lock()
_QUEUES: dict[str, collections.deque[tuple[str, str]]] = {}
_WORKERS: dict[str, threading.Thread] = {}

RunTurn = Callable[[str, str], Any]


def pending_followups(sid: str) -> int:
    with _REGISTRY:
        return len(_QUEUES.get(sid) or ())


def queue_followup(
    sid: str,
    text: str,
    *,
    life_dir: Path,
    run_turn: RunTurn,
) -> str:
    """Show ``text`` now and answer it after the running turn. Returns its turn id."""
    from .manager_pending_question import _emit_ui_turn

    body = str(text or "").strip()
    turn_id = f"web-{time.time_ns()}"
    _emit_ui_turn(
        Path(life_dir), "operator", body,
        message_id=f"{turn_id}-operator", metadata={"queued_while_running": True},
    )
    with _REGISTRY:
        _QUEUES.setdefault(sid, collections.deque()).append((turn_id, body))
        worker = _WORKERS.get(sid)
        if worker is None or not worker.is_alive():
            worker = threading.Thread(
                target=_drain, args=(sid, run_turn), name=f"argus-followup-{sid}", daemon=True,
            )
            _WORKERS[sid] = worker
            worker.start()
    return turn_id


def _drain(sid: str, run_turn: RunTurn) -> None:
    from .manager_state import _lock_for

    while True:
        with _REGISTRY:
            pending = _QUEUES.get(sid)
            if not pending:
                _QUEUES.pop(sid, None)
                _WORKERS.pop(sid, None)
                return
            turn_id, body = pending.popleft()
        # Wait for the running turn to finish first, so a Stop aimed at that
        # turn does not also discard what was typed while it ran.
        with _lock_for(sid):
            pass
        token = PRESHOWN_OPERATOR_IDS.set(frozenset({f"{turn_id}-operator"}))
        try:
            run_turn(body, turn_id)
        except Exception:  # noqa: BLE001 - one failed follow-up must not drop the rest
            log.warning("queued operator message for %s was not handled", sid, exc_info=True)
        finally:
            PRESHOWN_OPERATOR_IDS.reset(token)
