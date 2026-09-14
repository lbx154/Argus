from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import ContextManager

import pytest

from argus.core.session import session_lifecycle_lock, session_meta_lock


@pytest.mark.parametrize("lock", [session_meta_lock, session_lifecycle_lock])
def test_session_locks_serialize_threads(
    tmp_path: Path,
    lock: Callable[[Path, str], ContextManager[None]],
) -> None:
    owner_entered = threading.Event()
    release_owner = threading.Event()
    contender_entered = threading.Event()

    def owner() -> None:
        with lock(tmp_path, "s-portable"):
            owner_entered.set()
            release_owner.wait(timeout=5)

    def contender() -> None:
        with lock(tmp_path, "s-portable"):
            contender_entered.set()

    owner_thread = threading.Thread(target=owner)
    contender_thread = threading.Thread(target=contender)
    owner_thread.start()
    assert owner_entered.wait(timeout=2)
    contender_thread.start()
    try:
        assert not contender_entered.wait(timeout=0.1)
    finally:
        release_owner.set()
        owner_thread.join(timeout=2)
        contender_thread.join(timeout=2)

    assert contender_entered.is_set()


def test_session_locks_use_the_path_safe_session_id(tmp_path: Path) -> None:
    with session_meta_lock(tmp_path, "s-readable"):
        pass
    with session_lifecycle_lock(tmp_path, "s-readable"):
        pass

    assert (tmp_path / ".session-locks" / "s-readable.lock").is_file()
    assert (tmp_path / ".session-lifecycle-locks" / "s-readable.lock").is_file()


def test_session_locks_hash_names_that_would_exceed_windows_path_limits(
    tmp_path: Path,
) -> None:
    long_sid = "s-" + ("x" * 200)

    with session_meta_lock(tmp_path, long_sid):
        pass
    with session_lifecycle_lock(tmp_path, long_sid):
        pass

    meta_locks = list((tmp_path / ".session-locks").iterdir())
    lifecycle_locks = list((tmp_path / ".session-lifecycle-locks").iterdir())
    assert len(meta_locks) == len(lifecycle_locks) == 1
    assert len(meta_locks[0].name) < 80
    assert meta_locks[0].name == lifecycle_locks[0].name
