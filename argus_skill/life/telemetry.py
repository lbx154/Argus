"""Durable daemon-side mission telemetry.

Codex stream-json can go quiet while a long shell experiment is still
running. This module records an out-of-band heartbeat owned by the
argus-skill daemon itself, so operators can see subprocess and artifact
progress even when the LLM runner has not emitted a completed item yet.

The telemetry is intentionally local and compact:

* ``telemetry.jsonl`` keeps bounded heartbeat history.
* ``telemetry.status.json`` is the latest snapshot for cheap ``--status``.
* File contents are never copied; JSONL files are only newline-counted.
* Process command lines are redacted because argv may contain tokens.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TELEMETRY_FILE = "telemetry.jsonl"
TELEMETRY_STATUS_FILE = "telemetry.status.json"
SCHEMA_VERSION = 1

_DEFAULT_SCAN_DIRS: tuple[str, ...] = (
    "code",
    "scripts",
    "src",
    "benchmarks",
    "results",
    "runs",
    "logs",
    "out",
    "experiments",
    "bench",
    "research",
    "paper",
)
_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "site-packages",
    "dist",
    "build",
})
_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "credential",
    "key",
    "password",
    "passwd",
    "secret",
    "token",
)
_LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9_./+=:-]{48,}$")


def telemetry_interval_from_env(default: float = 10.0) -> float:
    """Return the heartbeat interval, bounded away from busy-polling."""
    raw = os.environ.get("ARGUS_SKILL_TELEMETRY_INTERVAL_S", "").strip()
    if not raw:
        return default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return default


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _truncate(text: str, limit: int = 240) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return repr(value)[:500]


def _split_scan_dirs(raw: str) -> list[str]:
    parts: list[str] = []
    for chunk in raw.replace(",", os.pathsep).split(os.pathsep):
        piece = chunk.strip()
        if piece:
            parts.append(piece)
    return parts


def _configured_scan_dirs(scan_dirs: Iterable[str] | None = None) -> tuple[str, ...]:
    if scan_dirs is not None:
        return tuple(str(part).strip() for part in scan_dirs if str(part).strip())
    raw = os.environ.get("ARGUS_SKILL_TELEMETRY_DIRS", "").strip()
    if raw:
        return tuple(_split_scan_dirs(raw))
    return _DEFAULT_SCAN_DIRS


def _redact_arg(arg: str, *, previous_secret: bool = False) -> tuple[str, bool]:
    text = str(arg or "")
    lowered = text.lower()
    marker_hit = any(marker in lowered for marker in _SECRET_MARKERS)
    if previous_secret:
        return "<redacted>", False
    if marker_hit:
        if "=" in text:
            key, _value = text.split("=", 1)
            return f"{key}=<redacted>", False
        return text, True
    if text.startswith(("sk-", "ghp_", "github_pat_")) or _LONG_BLOB_RE.match(text):
        return "<redacted>", False
    return text, False


def _redacted_cmdline(argv: list[str], *, limit: int = 180) -> str:
    redacted: list[str] = []
    redact_next = False
    for arg in argv[:12]:
        clean, redact_next = _redact_arg(arg, previous_secret=redact_next)
        redacted.append(clean)
    if len(argv) > len(redacted):
        redacted.append(f"...(+{len(argv) - len(redacted)} args)")
    return _truncate(" ".join(shlex.quote(arg) for arg in redacted), limit=limit)


def _read_proc_status(pid: int) -> dict[str, str] | None:
    status_path = Path("/proc") / str(pid) / "status"
    try:
        lines = status_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    out: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Name", "PPid", "State"}:
            out[key] = value.strip()
    return out


def _read_proc_cmdline(pid: int) -> list[str]:
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return []
    return [
        part.decode("utf-8", "replace")
        for part in raw.split(b"\0")
        if part
    ]


def collect_descendant_processes(
    root_pid: int,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Return a redacted summary of descendants of ``root_pid``.

    This is Linux-only and best-effort by design; callers get an empty
    process list on other platforms or if ``/proc`` is unavailable.
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return {"processes": [], "process_count": 0, "processes_truncated": 0}

    children: dict[int, list[int]] = {}
    states: dict[int, str] = {}
    names: dict[int, str] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        status = _read_proc_status(pid)
        if status is None:
            continue
        try:
            ppid = int(status.get("PPid", "0"))
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
        states[pid] = status.get("State", "")
        names[pid] = status.get("Name", "")

    descendants: list[int] = []
    queue = list(children.get(root_pid, []))
    seen: set[int] = set()
    while queue:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        queue.extend(children.get(pid, []))

    reported: list[dict[str, Any]] = []
    for pid in descendants[:limit]:
        argv = _read_proc_cmdline(pid)
        if argv:
            argv0 = Path(argv[0]).name or argv[0]
            cmd = _redacted_cmdline(argv)
            argc = len(argv)
        else:
            argv0 = names.get(pid, "") or "?"
            cmd = argv0
            argc = 0
        reported.append({
            "pid": pid,
            "argv0": _truncate(argv0, limit=80),
            "argc": argc,
            "state": states.get(pid, ""),
            "cmd": cmd,
        })

    return {
        "processes": reported,
        "process_count": len(descendants),
        "processes_truncated": max(0, len(descendants) - len(reported)),
    }


@dataclass
class _FileState:
    size: int
    mtime: float
    lines: int | None = None


class TelemetryRecorder:
    """Append compact telemetry history and publish the latest snapshot."""

    def __init__(self, life_dir: Path | str) -> None:
        self.life_dir = Path(life_dir).expanduser()
        self.history_path = self.life_dir / TELEMETRY_FILE
        self.status_path = self.life_dir / TELEMETRY_STATUS_FILE
        self._lock = threading.Lock()
        self.life_dir.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize(event)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                with self.history_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                self._write_status(payload)
            except OSError:
                pass
        return payload

    def _write_status(self, payload: dict[str, Any]) -> None:
        tmp = self.status_path.with_name(f"{self.status_path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.status_path)

    @staticmethod
    def _normalize(event: dict[str, Any]) -> dict[str, Any]:
        payload = _json_safe(dict(event))
        payload.setdefault("type", "life.telemetry")
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("ts", time.time())
        return payload


class MissionTelemetryMonitor:
    """Mission-scoped heartbeat thread.

    Tests can call :meth:`tick_once` directly for deterministic telemetry
    without starting a background thread.
    """

    def __init__(
        self,
        *,
        life_dir: Path | str,
        workdir: Path | str,
        item_id: str,
        title: str,
        interval_seconds: float | None = None,
        stop_event: threading.Event | None = None,
        root_pid: int | None = None,
        scan_dirs: Iterable[str] | None = None,
    ) -> None:
        self.life_dir = Path(life_dir).expanduser()
        self.workdir = Path(workdir).expanduser()
        self.item_id = str(item_id or "")
        self.title = str(title or "")
        self.interval_seconds = (
            telemetry_interval_from_env()
            if interval_seconds is None
            else max(1.0, float(interval_seconds))
        )
        self.stop_event = stop_event
        self.root_pid = int(root_pid or os.getpid())
        self.scan_dirs = _configured_scan_dirs(scan_dirs)
        self.max_files = _env_int("ARGUS_SKILL_TELEMETRY_MAX_FILES", 2000)
        self.max_scan_seconds = _env_float(
            "ARGUS_SKILL_TELEMETRY_SCAN_BUDGET_S",
            0.5,
            minimum=0.05,
        )
        self.max_processes = _env_int("ARGUS_SKILL_TELEMETRY_MAX_PROCS", 50)
        self.initial_jsonl_count_limit = _env_int(
            "ARGUS_SKILL_TELEMETRY_INITIAL_JSONL_BYTES",
            5 * 1024 * 1024,
        )
        self.jsonl_delta_limit = _env_int(
            "ARGUS_SKILL_TELEMETRY_JSONL_DELTA_BYTES",
            4 * 1024 * 1024,
        )
        self.recent_window_seconds = _env_float(
            "ARGUS_SKILL_TELEMETRY_RECENT_WINDOW_S",
            30 * 60.0,
            minimum=1.0,
        )
        self.recorder = TelemetryRecorder(self.life_dir)
        self._states: dict[Path, _FileState] = {}
        self._seq = 0
        self._started_at = time.time()
        self._local_stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.tick_once()
        self._thread = threading.Thread(
            target=self._run,
            name=f"argus-telemetry-{self.item_id[:8] or 'mission'}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._local_stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, min(10.0, self.interval_seconds * 2.0)))
        return self.tick_once(running=False)

    def tick_once(self, *, running: bool = True) -> dict[str, Any]:
        with self._tick_lock:
            now = time.time()
            self._seq += 1
            proc = (
                collect_descendant_processes(
                    self.root_pid,
                    limit=self.max_processes,
                )
                if running
                else {"processes": [], "process_count": 0, "processes_truncated": 0}
            )
            scan = self._scan_files(now) if running else {
                "files": [],
                "scanned_files": 0,
                "scan_ms": 0,
                "scan_truncated": False,
            }
            event: dict[str, Any] = {
                "type": "life.telemetry",
                "schema_version": SCHEMA_VERSION,
                "source": "mission_monitor",
                "seq": self._seq,
                "ts": now,
                "running": running,
                "item_id": self.item_id,
                "title": _truncate(self.title, limit=160),
                "workdir": str(self.workdir),
                "running_seconds": round(max(0.0, now - self._started_at), 1),
                **proc,
                **scan,
            }
            if not running:
                event["ended_ts"] = now
            return self.recorder.record(event)

    def _run(self) -> None:
        while not self._local_stop.wait(self.interval_seconds):
            if self.stop_event is not None and self.stop_event.is_set():
                break
            try:
                self.tick_once()
            except (OSError, RuntimeError, ValueError):
                continue

    def _scan_roots(self) -> list[Path]:
        roots: list[Path] = []
        workdir = self.workdir
        for raw in self.scan_dirs:
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    candidate.relative_to(workdir)
                except ValueError:
                    continue
            else:
                candidate = workdir / candidate
            if candidate.is_dir():
                roots.append(candidate)
        return roots

    def _scan_files(self, now: float) -> dict[str, Any]:
        started = time.monotonic()
        changed: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        for root in self._scan_roots():
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                dirnames[:] = [
                    name for name in dirnames
                    if name not in _IGNORE_DIRS and not name.startswith(".")
                ]
                if time.monotonic() - started > self.max_scan_seconds:
                    truncated = True
                    break
                for filename in filenames:
                    if scanned >= self.max_files:
                        truncated = True
                        break
                    path = Path(dirpath) / filename
                    scanned += 1
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    previous = self._states.get(path)
                    line_info, known_lines = self._jsonl_progress(path, previous, stat.st_size)
                    self._states[path] = _FileState(
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        lines=known_lines,
                    )
                    file_event = self._file_event(
                        path,
                        stat.st_size,
                        stat.st_mtime,
                        previous,
                        now,
                    )
                    if file_event is not None:
                        if line_info:
                            file_event.update(line_info)
                        changed.append(file_event)
                if truncated:
                    break
            if truncated:
                break
        changed.sort(
            key=lambda item: (float(item.get("mtime", 0.0)), int(item.get("size", 0))),
            reverse=True,
        )
        return {
            "files": changed[:12],
            "files_changed": len(changed),
            "scanned_files": scanned,
            "scan_ms": int((time.monotonic() - started) * 1000),
            "scan_truncated": truncated,
            "scan_dirs": list(self.scan_dirs),
        }

    def _file_event(
        self,
        path: Path,
        size: int,
        mtime: float,
        previous: _FileState | None,
        now: float,
    ) -> dict[str, Any] | None:
        if previous is None:
            if now - mtime > self.recent_window_seconds:
                return None
            size_delta = 0
            initial = True
        else:
            if previous.size == size and abs(previous.mtime - mtime) < 0.001:
                return None
            size_delta = size - previous.size
            initial = False
        try:
            rel = path.relative_to(self.workdir)
            rel_text = str(rel)
        except ValueError:
            rel_text = str(path)
        return {
            "path": rel_text,
            "size": size,
            "size_delta": size_delta,
            "mtime": mtime,
            "initial": initial,
        }

    def _jsonl_progress(
        self,
        path: Path,
        previous: _FileState | None,
        size: int,
    ) -> tuple[dict[str, Any], int | None]:
        if path.suffix.lower() != ".jsonl":
            return {}, previous.lines if previous is not None else None
        if previous is None:
            if size > self.initial_jsonl_count_limit:
                return {"line_count_skipped": "initial_file_too_large"}, None
            lines = _count_newlines(path, offset=0)
            if lines is None:
                return {}, None
            return {"line_count": lines, "new_lines": lines}, lines
        if size == previous.size:
            return (
                {"line_count": previous.lines}
                if previous.lines is not None
                else {},
                previous.lines,
            )
        if size < previous.size:
            if size > self.initial_jsonl_count_limit:
                return {"line_count_skipped": "file_rotated_too_large"}, None
            lines = _count_newlines(path, offset=0)
            if lines is None:
                return {}, None
            return {"line_count": lines, "rotated": True}, lines
        delta = size - previous.size
        if delta > self.jsonl_delta_limit:
            return {"new_lines_skipped": "delta_too_large"}, previous.lines
        new_lines = _count_newlines(path, offset=previous.size)
        if new_lines is None:
            return {}, previous.lines
        total = previous.lines + new_lines if previous.lines is not None else None
        info: dict[str, Any] = {"new_lines": new_lines}
        if total is not None:
            info["line_count"] = total
        return info, total


def _count_newlines(path: Path, *, offset: int) -> int | None:
    count = 0
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                count += chunk.count(b"\n")
    except OSError:
        return None
    return count


def read_latest_telemetry(life_dir: Path | str) -> dict[str, Any] | None:
    path = Path(life_dir).expanduser() / TELEMETRY_STATUS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "MissionTelemetryMonitor",
    "TelemetryRecorder",
    "TELEMETRY_FILE",
    "TELEMETRY_STATUS_FILE",
    "collect_descendant_processes",
    "read_latest_telemetry",
    "telemetry_interval_from_env",
]
