from __future__ import annotations

from pathlib import Path

from argus.core.session import session_lifecycle_lock, session_meta_lock


def test_session_locks_use_the_path_safe_session_id(tmp_path: Path) -> None:
    with session_meta_lock(tmp_path, "s-readable"):
        pass
    with session_lifecycle_lock(tmp_path, "s-readable"):
        pass

    assert (tmp_path / ".session-locks" / "s-readable.lock").is_file()
    assert (tmp_path / ".session-lifecycle-locks" / "s-readable.lock").is_file()


def test_long_session_ids_keep_their_readable_lock_name(tmp_path: Path) -> None:
    long_sid = "s-" + ("x" * 200)

    with session_meta_lock(tmp_path, long_sid):
        pass
    with session_lifecycle_lock(tmp_path, long_sid):
        pass

    assert [path.name for path in (tmp_path / ".session-locks").iterdir()] == [f"{long_sid}.lock"]
    assert [path.name for path in (tmp_path / ".session-lifecycle-locks").iterdir()] == [f"{long_sid}.lock"]
