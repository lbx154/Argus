"""Detached experiment launcher for durable on-disk run bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

_DEFAULT_ORPHAN_NETWORK_MAX_AGE = timedelta(minutes=15)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    out = [ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip()]
    slug = "".join(out).strip(".-_")
    return slug or "run"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _command_text(command: Iterable[str]) -> str:
    return shlex.join(list(command))


def _config_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_docker_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class LaunchSpec:
    run_root: Path
    run_id: str
    command: list[str]
    cwd: Path
    env: dict[str, str]
    metadata: dict[str, Any]
    preflight: dict[str, Any] | Callable[[], dict[str, Any] | None] | None = None
    stdout_log: str = "stdout.log"
    stderr_log: str = "stderr.log"


def build_manifest(spec: LaunchSpec) -> dict[str, Any]:
    run_dir = spec.run_root / spec.run_id
    env_snapshot = {key: value for key, value in sorted(spec.env.items())}
    metadata = _jsonable(spec.metadata)
    command = list(spec.command)
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "run_id": spec.run_id,
        "bundle_root": str(run_dir),
        "created_at": _utc_now(),
        "cwd": str(spec.cwd),
        "command": command,
        "command_text": _command_text(command),
        "env": env_snapshot,
        "metadata": metadata,
        "stdout_log": spec.stdout_log,
        "stderr_log": spec.stderr_log,
        "pid_path": "pid",
        "status_path": "status.json",
    }
    manifest["env_config_hash"] = _config_hash(
        {
            "command": command,
            "cwd": str(spec.cwd),
            "env": env_snapshot,
            "metadata": metadata,
        }
    )
    return manifest


def _status_payload(
    *,
    run_id: str,
    state: str,
    message: str | None = None,
    child_pid: int | None = None,
    exit_code: int | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "state": state,
        "updated_at": _utc_now(),
    }
    if message is not None:
        payload["message"] = message
    if child_pid is not None:
        payload["child_pid"] = child_pid
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if started_at is not None:
        payload["started_at"] = started_at
    if ended_at is not None:
        payload["ended_at"] = ended_at
    return payload


def _run_docker_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _inspect_docker_network(network_id: str) -> dict[str, Any] | None:
    try:
        result = _run_docker_command(["docker", "network", "inspect", network_id])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        log.debug("skipping docker network %s inspection: %s", network_id, exc)
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.debug("unable to parse docker network %s inspection: %s", network_id, exc)
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    return first if isinstance(first, dict) else None


def _compose_network_is_orphaned(
    record: dict[str, Any],
    *,
    max_age: timedelta,
    now: datetime,
) -> bool:
    labels = record.get("Labels")
    if not isinstance(labels, dict):
        return False
    if labels.get("com.docker.compose.network") != "default":
        return False
    if not labels.get("com.docker.compose.project"):
        return False
    if record.get("Driver") != "bridge":
        return False

    containers = record.get("Containers")
    if not isinstance(containers, dict) or containers:
        return False

    created = _parse_docker_timestamp(str(record.get("Created", "")))
    if created is None:
        return False
    return now - created >= max_age


def cleanup_orphan_compose_networks(
    *,
    max_age: timedelta = _DEFAULT_ORPHAN_NETWORK_MAX_AGE,
) -> list[str]:
    """Remove stale Compose default networks that have no attached containers."""

    try:
        result = _run_docker_command(["docker", "network", "ls", "--format", "{{.ID}}"])
    except FileNotFoundError:
        log.debug("docker is unavailable; skipping compose network cleanup")
        return []
    except subprocess.CalledProcessError as exc:
        log.warning("unable to list docker networks for cleanup: %s", exc)
        return []

    network_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    removed: list[str] = []
    now = datetime.now(timezone.utc)
    for network_id in network_ids:
        record = _inspect_docker_network(network_id)
        if record is None:
            continue
        if not _compose_network_is_orphaned(record, max_age=max_age, now=now):
            continue
        try:
            _run_docker_command(["docker", "network", "rm", network_id])
        except subprocess.CalledProcessError as exc:
            log.warning("failed to remove orphaned docker network %s: %s", network_id, exc)
            continue
        removed.append(network_id)
    if removed:
        log.info("reclaimed %d stale compose network(s): %s", len(removed), ", ".join(removed))
    return removed


def _write_initial_bundle(spec: LaunchSpec, manifest: dict[str, Any]) -> Path:
    run_dir = spec.run_root / spec.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / manifest["stdout_log"]).touch()
    (run_dir / manifest["stderr_log"]).touch()
    (run_dir / "pid").write_text("", encoding="utf-8")
    _atomic_write_json(run_dir / "manifest.json", manifest)
    _atomic_write_json(
        run_dir / "status.json",
        _status_payload(run_id=spec.run_id, state="launching"),
    )
    return run_dir


def _resolve_preflight(
    preflight: dict[str, Any] | Callable[[], dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    if preflight is None:
        return None
    if callable(preflight):
        return preflight()
    return preflight


def _record_blocked_launch(
    run_dir: Path,
    *,
    run_id: str,
    preflight: dict[str, Any],
    stderr_log: str = "stderr.log",
) -> dict[str, Any]:
    _atomic_write_json(run_dir / "preflight.json", _jsonable(preflight))
    message = str(preflight.get("message") or "preflight failed")
    exit_code_value = preflight.get("exit_code")
    exit_code = 1 if exit_code_value is None else int(exit_code_value)
    blocked_state = str(preflight.get("state") or "launch_failed")
    status = _status_payload(
        run_id=run_id,
        state=blocked_state,
        message=message,
        started_at=_utc_now(),
        ended_at=_utc_now(),
        exit_code=exit_code,
    )
    _atomic_write_json(run_dir / "status.json", status)
    if stderr_log:
        _atomic_write_text(run_dir / stderr_log, message + "\n")
    return status


def _worker_main(manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_dir = manifest_path.parent
    cwd = Path(str(manifest["cwd"]))
    command = [str(item) for item in manifest["command"]]
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in manifest.get("env", {}).items()})

    status_path = run_dir / str(manifest.get("status_path", "status.json"))
    pid_path = run_dir / str(manifest.get("pid_path", "pid"))
    stdout_path = run_dir / str(manifest.get("stdout_log", "stdout.log"))
    stderr_path = run_dir / str(manifest.get("stderr_log", "stderr.log"))

    started_at = _utc_now()
    _atomic_write_json(
        status_path,
        _status_payload(run_id=str(manifest["run_id"]), state="running", started_at=started_at),
    )

    try:
        stdout_fh = stdout_path.open("a", encoding="utf-8", errors="replace")
        stderr_fh = stderr_path.open("a", encoding="utf-8", errors="replace")
    except OSError as exc:
        _atomic_write_json(
            status_path,
            _status_payload(
                run_id=str(manifest["run_id"]),
                state="launch_failed",
                message=f"unable to open logs: {exc}",
                started_at=started_at,
                ended_at=_utc_now(),
                exit_code=1,
            ),
        )
        return 1

    with stdout_fh, stderr_fh:
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stdout_fh,
                stderr=stderr_fh,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            _atomic_write_json(
                status_path,
                _status_payload(
                    run_id=str(manifest["run_id"]),
                    state="launch_failed",
                    message=f"unable to start command: {exc}",
                    started_at=started_at,
                    ended_at=_utc_now(),
                    exit_code=1,
                ),
            )
            return 1

        _atomic_write_text(pid_path, f"{proc.pid}\n")
        try:
            exit_code = proc.wait()
        except BaseException as exc:  # pragma: no cover - signal path
            proc.kill()
            proc.wait()
            _atomic_write_json(
                status_path,
                _status_payload(
                    run_id=str(manifest["run_id"]),
                    state="failed",
                    message=f"worker interrupted: {exc}",
                    child_pid=proc.pid,
                    started_at=started_at,
                    ended_at=_utc_now(),
                    exit_code=130,
                ),
            )
            return 130

    state = "completed" if exit_code == 0 else "failed"
    _atomic_write_json(
        status_path,
        _status_payload(
            run_id=str(manifest["run_id"]),
            state=state,
            child_pid=proc.pid,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=_utc_now(),
        ),
    )
    return exit_code


def launch_detached(spec: LaunchSpec) -> Path:
    cleanup_orphan_compose_networks()
    manifest = build_manifest(spec)
    run_dir = _write_initial_bundle(spec, manifest)
    preflight = _resolve_preflight(spec.preflight)
    if preflight is not None:
        _atomic_write_json(run_dir / "preflight.json", _jsonable(preflight))
        exit_code_value = preflight.get("exit_code")
        exit_code = 1 if exit_code_value is None else int(exit_code_value)
        blocked_state = str(preflight.get("state") or "launch_failed")
        if blocked_state.startswith(("launch_failed", "preflight_blocked", "blocked")) or exit_code != 0:
            _record_blocked_launch(
                run_dir,
                run_id=spec.run_id,
                preflight=preflight,
                stderr_log=str(manifest["stderr_log"]),
            )
            return run_dir
    manifest_path = run_dir / "manifest.json"
    worker_cmd = [
        sys.executable,
        "-m",
        "benchmarks.experiment_launcher",
        "--worker",
        str(manifest_path),
    ]
    subprocess.Popen(
        worker_cmd,
        cwd=str(spec.cwd),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return run_dir


def _parse_metadata(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"invalid metadata entry: {item!r} (expected key=value)")
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worker",
        metavar="MANIFEST",
        help="Internal worker mode used by the detached launcher.",
    )
    parser.add_argument(
        "--run-root",
        default="experiments",
        help="Root directory for run bundles.",
    )
    parser.add_argument("--run-id", help="Explicit run id. Defaults to timestamped slug.")
    parser.add_argument(
        "--cwd",
        default=str(Path(__file__).resolve().parents[1]),
        help="Working directory for the launched command.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment entries to inject into the launched command.",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra manifest metadata entries.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to launch after `--`.",
    )
    args = parser.parse_args(argv)

    if args.worker:
        return _worker_main(Path(args.worker))

    if not args.command:
        raise SystemExit("missing command after `--`")
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("missing command after `--`")

    run_root = Path(args.run_root).resolve()
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_slug(command[0])}"
    env = {key: str(value) for key, value in _parse_metadata(args.env).items()}
    metadata = _parse_metadata(args.metadata)
    spec = LaunchSpec(
        run_root=run_root,
        run_id=run_id,
        command=command,
        cwd=Path(args.cwd).resolve(),
        env=env,
        metadata=metadata,
    )
    run_dir = launch_detached(spec)
    print(run_dir)
    print((run_dir / "manifest.json"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
