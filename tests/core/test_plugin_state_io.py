"""Concurrent plugin-center polling must not prevent Windows atomic updates."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.core import plugin_manager as manager


def test_polling_and_installer_state_writes_remain_atomic(tmp_path):
    path = tmp_path / "operation.json"
    manager.write_json(path, {"sequence": 0})
    stop = threading.Event()
    errors = []
    def read():
        try:
            while not stop.is_set():
                assert type(manager.read_json(path)["sequence"]) is int
        except BaseException as exc:
            errors.append(exc)
    with ThreadPoolExecutor(max_workers=3) as pool:
        workers = [pool.submit(read) for _ in range(2)]
        try:
            for index in range(1, 151):
                manager.write_json(path, {"sequence": index})
        finally:
            stop.set()
        for worker in workers:
            worker.result(timeout=10)
    assert not errors
    assert manager.read_json(path) == {"sequence": 150}
    assert not list(tmp_path.glob("*.tmp"))


def test_permanent_denial_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "operation.json"
    manager.write_json(path, {"status": "previous"})
    def deny(*args):
        raise PermissionError("denied")
    monkeypatch.setattr(manager.os, "replace", deny)
    monkeypatch.setattr(manager.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        manager.write_json(path, {"status": "next"})
    assert manager.read_json(path) == {"status": "previous"}
    assert not list(tmp_path.glob("*.tmp"))


def test_published_job_is_not_dead_before_thread_start(tmp_path, monkeypatch):
    thread = threading.Thread(target=lambda: None)
    key = (str(tmp_path), "fixture")
    monkeypatch.setitem(manager._jobs, key, thread)
    assert manager._job_alive(tmp_path, "fixture", {"pid": manager.os.getpid()})
