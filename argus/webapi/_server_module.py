"""Late-bound access to the ``server`` module for its split-out siblings.

``server.py`` was split by API domain, and the pieces still call back into it
for things like ``read_daemon_status`` or ``stop_daemon``. Importing it at
module scope would both close the import cycle and bind the names at import
time, so a test that monkeypatches ``server.<dep>`` would not be seen. Four
modules each carried their own copy of this three-line resolver.
"""
from __future__ import annotations

from typing import Any


def server_module() -> Any:
    """Resolve ``webapi.server`` at call time so monkeypatching still works."""
    from . import server

    return server


__all__ = ["server_module"]
