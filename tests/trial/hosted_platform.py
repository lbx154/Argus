"""Explicit capability marks for hosted-trial tests (see conftest.py)."""
from __future__ import annotations

import os
import socket
import sys

import pytest

_HOST = "the hosted trial service runs on its Linux operator host"

needs_fifo = pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason=f"creates a FIFO to prove special files are refused; this platform has no os.mkfifo; {_HOST}",
)
needs_pread = pytest.mark.skipif(
    not hasattr(os, "pread"),
    reason=f"interposes os.pread, which this platform lacks; {_HOST}",
)
needs_posix_modes = pytest.mark.skipif(
    os.name != "posix",
    reason=f"asserts POSIX owner-only file modes; this platform uses ACLs instead; {_HOST}",
)
needs_posix_accounts = pytest.mark.skipif(
    not hasattr(os, "getuid"),
    reason=f"provisions tenant files for POSIX user and group IDs; this platform has no os.getuid; {_HOST}",
)
needs_unix_sockets = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason=f"uses Unix domain sockets, which this platform lacks; {_HOST}",
)
needs_linux_proc = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=f"verifies peers through Linux /proc process identity; {_HOST}",
)
hosted_posix_paths = pytest.mark.skipif(
    os.name != "posix",
    reason=f"renders hosted container prompts with this host's path flavour; hosted paths are POSIX; {_HOST}",
)
# The hosted gateway's admission, slot and disconnect deadlines are 50 ms to 1 s.
# Windows runners miss them (single requests time out in admission), and the
# gateway is never deployed there.
linux_host_deadlines = pytest.mark.skipif(
    sys.platform == "win32",
    reason=f"sub-second gateway admission and disconnect deadlines are not met on Windows runners; {_HOST}",
)
