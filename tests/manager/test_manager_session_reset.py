"""An explicit conversation reset drops the Manager's persistent session pointer."""
from __future__ import annotations

import json

from argus.manager import reset_manager_session
from argus.manager._session_ops import _SESSION_FILE, _ManagerSession


def _write_session(root, tid: str = "thread-abc") -> None:
    (root / _SESSION_FILE).write_text(json.dumps({"thread_id": tid}), encoding="utf-8")


def test_reset_clears_persisted_session(tmp_path):
    _write_session(tmp_path)
    # Before reset a Manager built on this root WOULD resume the old thread.
    assert _ManagerSession(runner=None, project_root=tmp_path).thread_id == "thread-abc"
    # The reset drops the pointer...
    assert reset_manager_session(tmp_path) is True
    assert not (tmp_path / _SESSION_FILE).exists()
    # ...so the next Manager session starts fresh (nothing to resume).
    assert _ManagerSession(runner=None, project_root=tmp_path).thread_id is None


def test_reset_is_idempotent_and_fail_soft(tmp_path):
    # No session yet → returns False, never raises.
    assert reset_manager_session(tmp_path) is False
    _write_session(tmp_path)
    assert reset_manager_session(tmp_path) is True
    # A second reset on the now-empty root is safe.
    assert reset_manager_session(tmp_path) is False


def test_reset_exported_from_manager_package():
    # The web server's conversation reset imports it from the package.
    import argus.manager as m

    assert hasattr(m, "reset_manager_session")
    assert "reset_manager_session" in m.__all__
