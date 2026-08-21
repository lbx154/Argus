"""One process-wide stop flag, so a long wait can notice a signal.

The daemon's stop is cooperative and lands between missions. A round that
yields to external work waits inside a mission, for as long as that work
keeps running, so it never reaches a boundary on its own. The flag makes the
request readable from inside the wait itself.
"""

from __future__ import annotations

import threading

_stopping = threading.Event()


def request_stop() -> None:
    """Record that this process has been asked to stop."""
    _stopping.set()


def stop_requested() -> bool:
    """Report whether a stop is pending, for loops that outlive a mission."""
    return _stopping.is_set()


def clear_stop() -> None:
    """Forget a previous request, for interpreters that host more than one run."""
    _stopping.clear()


__all__ = ["clear_stop", "request_stop", "stop_requested"]
