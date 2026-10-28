"""Concurrent plugin-center polling must not prevent Windows atomic updates."""

import pytest

from argus.core import plugin_manager as manager


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
    assert [entry.name for entry in tmp_path.iterdir()] == ["operation.json"]
