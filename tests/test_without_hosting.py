"""Argus core runs with the hosted trial absent, as it will once the trial leaves the tree."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A Manager turn that hands a ticket to the team through the web app, and
# Copilot agent calls through the CLI backend: the paths that ask core/hosting.
CORE_TESTS = [
    "tests/webapi/test_dispatch_receipt_truth.py"
    "::test_real_dispatch_ack_does_not_invent_execution_or_repeat_the_confirmation[ready-False]",
    "tests/test_copilot_session_binding.py",
]

WITHOUT_TRIAL = """
import sys
sys.modules["argus.trial"] = None  # import argus.trial now fails as if it were not installed
from argus.core.hosting import NO_HOSTING, hosting
assert hosting() is NO_HOSTING
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def test_core_runs_a_manager_turn_and_team_dispatch_without_the_trial(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", WITHOUT_TRIAL, "-p", "no:cacheprovider", "-p", "no:xdist",
         f"--basetemp={tmp_path / 'bt'}", *CORE_TESTS],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert " passed" in result.stdout and "skipped" not in result.stdout
