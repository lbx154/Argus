"""A backend that is installed but not logged in must not read as ready.

Doctor is the first thing a new user runs. It probed the binary and its
version by default and left the login check behind `--deep`, so a box with
codex on PATH and no credentials passed every blocking check and then failed
on the user's first real task.
"""

from __future__ import annotations

import re
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1] / "argus_skill/apps/cli/_core.py"


def test_doctor_does_not_hide_the_login_check_behind_a_flag() -> None:
    source = _CORE.read_text(encoding="utf-8")

    assert 'probe_auth=bool(getattr(args, "deep", False))' not in source
    assert source.count("probe_auth=True") >= 4


def test_every_doctor_entry_point_probes_auth() -> None:
    """Several entry points build a report; a missed one is a quiet hole."""
    source = _CORE.read_text(encoding="utf-8")
    calls = re.findall(r"run_full_doctor\((?:[^()]|\([^()]*\))*\)", source, re.S)

    assert calls, "no doctor invocations found — did the call shape change?"
    for call in calls:
        assert "probe_auth=True" in call, call[:120]
