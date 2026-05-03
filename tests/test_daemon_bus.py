"""Tests for ``argus_skill.daemon.bus`` (JSONL bus + status helpers)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.daemon.bus import (
    BusCommand,
    JsonlCommandBus,
    inspect_daemon_status,
    read_status,
    write_status,
)


def _cmd(kind: str, text: str = "") -> BusCommand:
    return BusCommand(kind=kind, text=text, source="test", ts=time.time())


def test_publish_and_read_new_round_trip(tmp_path: Path) -> None:
    bus_path = tmp_path / "inbox.jsonl"
    bus = JsonlCommandBus(str(bus_path))
    bus.publish(_cmd("run", "hello"))
    bus.publish(_cmd("inject", "add tests"))
    items = bus.read_new()
    kinds = [item.kind for item in items]
    texts = [item.text for item in items]
    assert kinds == ["run", "inject"]
    assert texts == ["hello", "add tests"]
    # Subsequent read returns nothing — offset advanced.
    assert bus.read_new() == []


def test_publish_appends_jsonl_line(tmp_path: Path) -> None:
    bus_path = tmp_path / "inbox.jsonl"
    bus = JsonlCommandBus(str(bus_path))
    bus.publish(_cmd("stop"))
    raw = bus_path.read_text(encoding="utf-8")
    line = raw.strip().splitlines()[0]
    payload = json.loads(line)
    assert payload["kind"] == "stop"


def test_status_round_trip(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    payload = {
        "daemon_running": True,
        "daemon_pid": os.getpid(),
        "current_status": "idle",
    }
    write_status(str(status_path), payload)
    loaded = read_status(str(status_path))
    assert loaded["daemon_pid"] == os.getpid()
    assert loaded["current_status"] == "idle"


def test_inspect_daemon_status_alive_for_self_pid(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    write_status(str(status_path), {
        "daemon_running": True,
        "daemon_pid": os.getpid(),
        "current_status": "idle",
        "updated_at": _now_iso(),
    })
    inspection = inspect_daemon_status(str(status_path), stale_after_seconds=60)
    assert inspection.is_live is True
    assert inspection.daemon_pid == os.getpid()


def test_inspect_daemon_status_missing_file(tmp_path: Path) -> None:
    inspection = inspect_daemon_status(str(tmp_path / "nope.json"))
    assert inspection.is_live is False
    assert inspection.reason is not None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

