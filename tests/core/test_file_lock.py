from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from argus_skill.core.file_lock import exclusive_file_lock


def test_exclusive_file_lock_times_out_under_contention(tmp_path: Path) -> None:
    path = tmp_path / "contended.lock"
    with path.open("a+") as owner, path.open("a+") as contender:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="timed out acquiring test lock"):
            with exclusive_file_lock(
                contender,
                timeout_seconds=0.01,
                poll_seconds=0.001,
                lock_name="test lock",
            ):
                pytest.fail("contended lock must not be acquired")
