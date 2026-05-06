import json
import threading
import time
from pathlib import Path

from argus_skill.apps import up_app


def test_wait_no_status(tmp_path):
    assert up_app._wait_for_daemon_up(tmp_path, timeout=0.3) is False


def test_wait_returns_true_on_fresh_running_status(tmp_path):
    spawn_t = time.time()
    (tmp_path / "status.json").write_text(json.dumps({"daemon_running": True, "daemon_pid": 999}))
    assert up_app._wait_for_daemon_up(tmp_path, timeout=1.0, expect_pid=999, spawn_time=spawn_t) is True


def test_wait_rejects_stale_status(tmp_path):
    # status.json was written before spawn — should not be accepted.
    (tmp_path / "status.json").write_text(json.dumps({"daemon_running": True, "daemon_pid": 111}))
    time.sleep(0.05)
    spawn_t = time.time() + 0.5  # spawn is in the future
    assert up_app._wait_for_daemon_up(tmp_path, timeout=0.3, expect_pid=111, spawn_time=spawn_t) is False


def test_wait_polls_until_fresh(tmp_path):
    spawn_t = time.time()
    def writer():
        time.sleep(0.3)
        (tmp_path / "status.json").write_text(json.dumps({"daemon_running": True, "daemon_pid": 777}))
    threading.Thread(target=writer, daemon=True).start()
    assert up_app._wait_for_daemon_up(tmp_path, timeout=2.0, expect_pid=777, spawn_time=spawn_t) is True


def test_wait_rejects_pid_mismatch(tmp_path):
    spawn_t = time.time()
    (tmp_path / "status.json").write_text(json.dumps({"daemon_running": True, "daemon_pid": 1}))
    assert up_app._wait_for_daemon_up(tmp_path, timeout=0.3, expect_pid=999, spawn_time=spawn_t) is False
