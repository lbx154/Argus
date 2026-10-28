"""Hosted-trial test support across operating systems.

The hosted trial service runs on its Linux operator host. When a hosted
operation reaches a capability this platform lacks, the product raises
``HostedHostRequired`` naming that capability; such a test is reported as
skipped with that exact reason. On Linux the same exception is a real failure.
Tests that need a capability directly (FIFOs, POSIX modes, Unix sockets) use
the explicit marks in ``hosted_platform``.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from hosted_platform import runtime_pythonpath

from argus.core import hosting
from argus_trial_server.hosted_host import HostedHostRequired
from tests import conftest as _argus_suite

# Argus's suite-wide guards (throwaway state roots and working directory, no
# host sandbox, no Copilot probe, no stop-flag leaks) and its shared fixtures
# apply here exactly as they did when these tests lived under tests/trial/.
globals().update({name: value for name, value in vars(_argus_suite).items() if not name.startswith("__")})

# Argus finds the runtime by its entry point, as on the hosting machine.
sys.path.extend(path for path in runtime_pythonpath().split(os.pathsep) if path not in sys.path)
hosting.hosted_runtime.cache_clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (call.excinfo is not None and not sys.platform.startswith("linux")
            and call.excinfo.errisinstance(HostedHostRequired)):
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {call.excinfo.value}")


@pytest.fixture
def socket_dir():
    """A short directory for Unix socket paths.

    macOS limits an AF_UNIX path to 104 bytes and its per-user temporary
    directory alone uses about half of that, so pytest's tmp_path is too long.
    """
    path = Path(tempfile.mkdtemp(prefix="ag-", dir="/tmp" if Path("/tmp").is_dir() else None))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
