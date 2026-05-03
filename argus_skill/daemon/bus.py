from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DAEMON_STATUS_STALE_SECONDS = 15


@dataclass
class BusCommand:
    kind: str
    text: str
    source: str
    ts: float


class JsonlCommandBus:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._offset = 0
        if self.path.exists():
            self._offset = self.path.stat().st_size

    def publish(self, command: BusCommand) -> None:
        line = json.dumps(asdict(command), ensure_ascii=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_new(self) -> list[BusCommand]:
        if not self.path.exists():
            return []
        out: list[BusCommand] = []
        with self._lock:
            with self.path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                lines = f.readlines()
                self._offset = f.tell()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            kind = payload.get("kind")
            text = payload.get("text", "")
            source = payload.get("source", "unknown")
            ts = payload.get("ts", time.time())
            if not isinstance(kind, str):
                continue
            if not isinstance(text, str):
                text = str(text)
            if not isinstance(source, str):
                source = str(source)
            if not isinstance(ts, (int, float)):
                ts = time.time()
            out.append(BusCommand(kind=kind, text=text, source=source, ts=float(ts)))
        return out


@dataclass
class DaemonStatusInspection:
    payload: dict[str, Any] | None
    is_live: bool
    reason: str | None
    daemon_pid: int | None
    updated_at: datetime | None


def write_status(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def read_status(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def inspect_daemon_status(
    path: str | Path,
    *,
    stale_after_seconds: int = DEFAULT_DAEMON_STATUS_STALE_SECONDS,
    now: datetime | None = None,
) -> DaemonStatusInspection:
    return inspect_daemon_status_payload(
        read_status(path),
        stale_after_seconds=stale_after_seconds,
        now=now,
    )


def inspect_daemon_status_payload(
    payload: dict[str, Any] | None,
    *,
    stale_after_seconds: int = DEFAULT_DAEMON_STATUS_STALE_SECONDS,
    now: datetime | None = None,
) -> DaemonStatusInspection:
    timestamp = parse_status_timestamp(payload.get("updated_at")) if isinstance(payload, dict) else None
    daemon_pid = _coerce_pid(payload.get("daemon_pid")) if isinstance(payload, dict) else None
    if payload is None:
        return DaemonStatusInspection(
            payload=None,
            is_live=False,
            reason="No daemon status found.",
            daemon_pid=None,
            updated_at=None,
        )
    if payload.get("daemon_running") is not True:
        return DaemonStatusInspection(
            payload=payload,
            is_live=False,
            reason="Daemon is not running.",
            daemon_pid=daemon_pid,
            updated_at=timestamp,
        )
    if daemon_pid is None:
        return DaemonStatusInspection(
            payload=payload,
            is_live=False,
            reason="Daemon status is missing a valid daemon_pid.",
            daemon_pid=None,
            updated_at=timestamp,
        )
    if not is_pid_running(daemon_pid):
        return DaemonStatusInspection(
            payload=payload,
            is_live=False,
            reason=f"Daemon status points to dead pid {daemon_pid}.",
            daemon_pid=daemon_pid,
            updated_at=timestamp,
        )
    if timestamp is None:
        return DaemonStatusInspection(
            payload=payload,
            is_live=False,
            reason="Daemon status is missing a valid updated_at timestamp.",
            daemon_pid=daemon_pid,
            updated_at=None,
        )
    reference_now = now or datetime.now(timezone.utc)
    age_seconds = (reference_now - timestamp).total_seconds()
    if age_seconds > stale_after_seconds:
        return DaemonStatusInspection(
            payload=payload,
            is_live=False,
            reason=f"Daemon status is stale ({int(age_seconds)}s old).",
            daemon_pid=daemon_pid,
            updated_at=timestamp,
        )
    return DaemonStatusInspection(
        payload=payload,
        is_live=True,
        reason=None,
        daemon_pid=daemon_pid,
        updated_at=timestamp,
    )


def parse_status_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if completed.returncode != 0:
            return False
        return str(pid) in (completed.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _coerce_pid(raw: object) -> int | None:
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else None
    return None
