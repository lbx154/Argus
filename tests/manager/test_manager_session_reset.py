"""New daemon = fresh Manager session.

EN: ``reset_manager_session`` drops the persistent codex thread pointer so a
fresh daemon does NOT resume the prior generation's Manager conversation — the
#1 cross-daemon context-pollution vector. The daemon wires this at boot, before
its first ``Manager.divide()``.
中文：``reset_manager_session`` 清掉常驻 codex thread 指针，让新 daemon 不再
resume 上一代 daemon 的 Manager 会话（头号跨 daemon 上下文污染）。daemon 在
启动时、第一次 ``Manager.divide()`` 之前调用它。
"""
from __future__ import annotations

import json

from argus_skill.manager import reset_manager_session
from argus_skill.manager._core import _SESSION_FILE, _ManagerSession


def _write_session(root, tid: str = "thread-abc") -> None:
    (root / _SESSION_FILE).write_text(json.dumps({"thread_id": tid}), encoding="utf-8")


def test_reset_clears_persisted_session(tmp_path):
    _write_session(tmp_path)
    # Before reset a Manager built on this root WOULD resume the old thread.
    assert _ManagerSession(runner=None, project_root=tmp_path).thread_id == "thread-abc"
    # The daemon-boot reset drops the pointer...
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
    # The daemon imports it via `from ..manager import reset_manager_session`.
    import argus_skill.manager as m

    assert hasattr(m, "reset_manager_session")
    assert "reset_manager_session" in m.__all__
