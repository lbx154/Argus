"""``os.replace`` that tolerates Windows' transient sharing violations."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Windows refuses to replace a file another handle still has open without
# FILE_SHARE_DELETE (antivirus and indexer scans of a just-written file are the
# usual holders). Those holds last milliseconds, so a short bounded retry turns
# them into a wait instead of a failed write.
_RETRY_SECONDS = 2.0
_RETRY_POLL_SECONDS = 0.02


def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Atomically move ``source`` over ``target``."""
    if sys.platform != "win32":
        os.replace(source, target)
        return
    deadline = time.monotonic() + _RETRY_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline or not Path(source).exists():
                raise
            time.sleep(_RETRY_POLL_SECONDS)
