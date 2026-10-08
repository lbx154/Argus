"""Hosted-trial test support across operating systems.

The hosted trial service runs on its Linux operator host. When a hosted
operation reaches a capability this platform lacks, the product raises
``HostedHostRequired`` naming that capability; such a test is reported as
skipped with that exact reason. On Linux the same exception is a real failure.
Tests that need a capability directly (FIFOs, POSIX modes, Unix sockets) use
the explicit marks in ``hosted_platform``.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from argus.trial.hosted_host import HostedHostRequired


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
