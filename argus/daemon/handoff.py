"""Blue/green daemon handoff with standby and per-run rollback."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..agent_cli._process_control import windows_hidden_subprocess_kwargs
from ..core.atomic_replace import write_atomic
from ..core.daemon_lock import (
    DaemonAlreadyRunning,
    acquire_global_daemon_lock,
    is_pid_running,
)
from .config import LifeWorkerConfig
from .config import config_from_payload as _config_from_payload
from .config import config_payload as _config_payload
from .state import (
    _daemon_log_path,
    _daemon_pid_path,
    _daemon_status_path,
    _daemon_status_payload,
    _new_boot_id,
    _point_active_daemon_log,
    _truthy_env,
)

log = logging.getLogger(__name__)

_HANDOFF_CONFIG_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG"
_HANDOFF_READY_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_READY"
_HANDOFF_TOKEN_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN"
_HANDOFF_LOG_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_LOG"
_HANDOFF_ROLLBACK_SOURCE_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_ROLLBACK_SOURCE"
_HANDOFF_INCUMBENT_PID_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_INCUMBENT_PID"
_DEPLOYMENT_ROLL_FILE = "daemon-roll.json"
# One lock attempt's patience once the lock should be free. Between attempts
# the candidate checks the incumbent is still alive; there is no overall cap.
_HANDOFF_LOCK_ATTEMPT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Blue/green self-handoff
# ---------------------------------------------------------------------------


def _record_handoff_alert(config: LifeWorkerConfig, text: str, *, reason: str) -> None:
    """Leave the operator a durable, visible note that a handoff went wrong.

    A dropped deployment, or a project left without a daemon, must not live
    only in a daemon log nobody reads; ``events.jsonl`` is the timeline the
    operator sees.
    """
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    # The project's state root: where its daemon's own sink writes, and where
    # the deployment request this handoff carried was read.
    try:
        JsonlEventSink(None, life_dir=Path(config.life_dir)).append({
            "type": EventType.OPERATOR_ALERT,
            "operator_alert": True,
            "reason": reason,
            "text": text,
        })
    except Exception:  # noqa: BLE001 - the log line below still records it
        log.exception("daemon handoff: could not record the operator alert")
    log.error("daemon handoff: %s", text)

def _strip_git_config_injection(env: MutableMapping[str, str]) -> list[str]:
    """Remove the ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_*`` /
    ``GIT_CONFIG_VALUE_*`` env-based config-injection family in place.

    The host seeds a benign ``safe.bareRepository=explicit`` override via
    these vars, but the codex sandbox forwards ``GIT_CONFIG_COUNT`` /
    ``GIT_CONFIG_VALUE_0`` while dropping ``GIT_CONFIG_KEY_0`` — leaving an
    incomplete tuple that makes *every* ``git`` command in the agent's shell
    fail with ``fatal: unable to parse command-line config`` until the agent
    rediscovers an ``env -u`` workaround, burning rounds each mission. The
    agent's project git work does not need this host override, so drop the
    whole family from the env handed to child shells.

    Returns the list of removed keys (for logging/tests).
    """
    removed = [
        k
        for k in list(env)
        if k == "GIT_CONFIG_COUNT"
        or k.startswith("GIT_CONFIG_KEY_")
        or k.startswith("GIT_CONFIG_VALUE_")
    ]
    for k in removed:
        env.pop(k, None)
    return removed


def _handoff_ready_path(life_dir: Path) -> Path:
    return life_dir / "daemon.handoff.json"


def _handoff_config_path(life_dir: Path, token: str) -> Path:
    return life_dir / f"daemon.handoff.{token}.json"


def request_deployment_handoff(receipt_dir: Path, candidate_source_root: Path) -> None:
    """Offer one fully deployed runtime to the daemon at its next boundary."""
    receipt_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(
        receipt_dir / _DEPLOYMENT_ROLL_FILE,
        json.dumps({"candidate_source_root": str(candidate_source_root.resolve())}),
    )


def _consume_deployment_handoff(life_dir: Path) -> Path | None:
    request = life_dir / "maintenance" / "receipts" / _DEPLOYMENT_ROLL_FILE
    claimed = request.with_suffix(".consumed")
    try:
        request.replace(claimed)
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(claimed.read_text(encoding="utf-8"))
        return Path(payload["candidate_source_root"]).expanduser().resolve(strict=True)
    finally:
        claimed.unlink(missing_ok=True)


def _spawn_handoff_candidate(
    config: LifeWorkerConfig,
    *,
    reason: str,
    standby_timeout: float = 30.0,
    candidate_source_root: Path | None = None,
    rollback_source_root: Path | None = None,
) -> int:
    """Start a fresh interpreter and wait until it reaches standby.

    Returns the standby candidate's pid, or 0 when it did not get there.
    """
    token = uuid.uuid4().hex
    config.life_dir.mkdir(parents=True, exist_ok=True)
    ready_path = _handoff_ready_path(config.life_dir)
    config_path = _handoff_config_path(config.life_dir, token)
    ready_path.unlink(missing_ok=True)
    payload = {
        "token": token,
        "reason": reason,
        "config": _config_payload(config),
    }
    try:
        config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("daemon handoff: failed to write config")
        return 0
    boot_id = _new_boot_id()
    log_path = _daemon_log_path(config.life_dir, config.log_path, boot_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env[_HANDOFF_CONFIG_ENV] = str(config_path)
    env[_HANDOFF_READY_ENV] = str(ready_path)
    env[_HANDOFF_TOKEN_ENV] = token
    env[_HANDOFF_LOG_ENV] = str(log_path)
    # The candidate keeps waiting for the daemon lock while this process lives.
    env[_HANDOFF_INCUMBENT_PID_ENV] = str(os.getpid())
    if candidate_source_root is not None:
        candidate_root = Path(candidate_source_root).expanduser().resolve()
        pythonpath = [
            str(candidate_root),
            *[
                part
                for part in env.get("PYTHONPATH", "").split(os.pathsep)
                if part and Path(part).expanduser().resolve() != candidate_root
            ],
        ]
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        env["ARGUS_SKILL_SOURCE_ROOT"] = str(candidate_root)
        env["ARGUS_SKILL_SELF_MANAGED_SOURCE"] = "1"
    if rollback_source_root is not None:
        env[_HANDOFF_ROLLBACK_SOURCE_ENV] = str(
            Path(rollback_source_root).expanduser().resolve()
        )
    else:
        env.pop(_HANDOFF_ROLLBACK_SOURCE_ENV, None)
    cmd = [
        sys.executable,
        "-c",
        (
            "from argus.daemon.life_worker import run_handoff_child; "
            "raise SystemExit(run_handoff_child())"
        ),
    ]
    try:
        with log_path.open("ab") as log_fh:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=None if os.name == "nt" else "/",
                env=env,
                close_fds=True,
                # This recovery candidate can be launched by a GUI-hosted
                # daemon. Keep its non-interactive bootstrap off-screen.
                **windows_hidden_subprocess_kwargs(),
            )
    except OSError:
        log.exception("daemon handoff: failed to spawn candidate")
        config_path.unlink(missing_ok=True)
        return 0

    deadline = time.monotonic() + standby_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.warning("daemon handoff: candidate exited early rc=%s", proc.returncode)
            config_path.unlink(missing_ok=True)
            return 0
        try:
            data = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            time.sleep(0.1)
            continue
        if data.get("token") == token and data.get("state") == "standby":
            return int(proc.pid)
        time.sleep(0.1)
    log.warning("daemon handoff: candidate did not reach standby in %.1fs", standby_timeout)
    try:
        proc.terminate()
    except OSError:
        pass
    config_path.unlink(missing_ok=True)
    ready_path.unlink(missing_ok=True)
    return 0


def _acquire_daemon_lock_with_timeout(
    pid_path: Path,
    timeout: float,
    *,
    acquire_fn: Callable[..., Any] = acquire_global_daemon_lock,
) -> Any:
    deadline = time.monotonic() + timeout
    last_exc: DaemonAlreadyRunning | None = None
    while True:
        try:
            return acquire_fn(pid_path=pid_path)
        except DaemonAlreadyRunning as exc:
            last_exc = exc
            if time.monotonic() >= deadline:
                raise last_exc
            time.sleep(0.1)


def run_handoff_child_process(
    *,
    worker_factory: Callable[[LifeWorkerConfig], Any],
    acquire_lock: Callable[[Path, float], Any],
) -> int:
    """Entrypoint for a blue/green handoff candidate."""
    config_env = os.environ.get(_HANDOFF_CONFIG_ENV, "")
    ready_env = os.environ.get(_HANDOFF_READY_ENV, "")
    token = os.environ.get(_HANDOFF_TOKEN_ENV, "")
    if not config_env or not ready_env or not token:
        sys.stderr.write("argus handoff: missing handoff environment\n")
        return 2
    config_path = Path(config_env).expanduser()
    ready_path = Path(ready_env).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        config = _config_from_payload(payload["config"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(f"argus handoff: invalid config: {exc}\n")
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    worker = worker_factory(config)
    # Housekeeping: prune stale projects on daemon boot too (covers a daemon
    # started directly via --daemon, not just via the cockpit). Best-effort.
    try:
        from ..core.project_gc import maybe_gc_stale_projects
        # Exclude THIS daemon's own project: the sweep runs before daemon.pid is
        # acquired, so a freshly-resumed long-parked project would otherwise be
        # trashed out from under the daemon starting on it.
        _fp = getattr(config, "project_fingerprint", "") or ""
        maybe_gc_stale_projects(
            getattr(config, "global_root", None),
            exclude={_fp} if _fp else None,
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        ready_path.write_text(
            json.dumps({
                "token": token,
                "state": "standby",
                "pid": os.getpid(),
                "ts": time.time(),
            }),
            encoding="utf-8",
        )
    except OSError as exc:
        sys.stderr.write(f"argus handoff: failed to write standby file: {exc}\n")
        return 2

    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        incumbent_pid = int(os.environ.get(_HANDOFF_INCUMBENT_PID_ENV, "") or 0)
    except ValueError:
        incumbent_pid = 0
    while True:
        try:
            lock = acquire_lock(pid_path, _HANDOFF_LOCK_ATTEMPT_SECONDS)
            break
        except DaemonAlreadyRunning as exc:
            # An incumbent from a release before the variable above names
            # itself only as the lock holder, so the first holder seen is it.
            if incumbent_pid <= 0 and exc.pid:
                incumbent_pid = int(exc.pid)
            # The incumbent holds the lock until it has drained, however long
            # that takes. Giving up while it still lived stranded the project:
            # the incumbent exited moments later with no successor left. Only
            # a lock now held by some other process ends the wait.
            if (
                incumbent_pid > 0
                and exc.pid in (None, incumbent_pid)
                and is_pid_running(incumbent_pid)
            ):
                log.info(
                    "handoff candidate: incumbent pid=%s is still draining; waiting",
                    incumbent_pid,
                )
                continue
            _record_handoff_alert(
                config,
                f"A runtime handoff ({payload.get('reason') or 'unspecified'}) was "
                f"dropped: the daemon lock is held by pid {exc.pid or 'unknown'}, "
                "not the daemon that started the handoff. That daemon keeps "
                "running; the handed-off runtime was not adopted.",
                reason="daemon_handoff_lock_taken",
            )
            return 2

    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
        ready_path.unlink(missing_ok=True)
        config_path.unlink(missing_ok=True)
    except OSError:
        log.exception("handoff candidate: failed to publish active status")

    # This candidate just took over — repoint daemon.log at its own boot log so
    # readers follow the live process (the incumbent's boot log is left intact,
    # never interleaved). Fail-soft: skip if the handoff log env is absent.
    _hlog = os.environ.get(_HANDOFF_LOG_ENV, "")
    if _hlog:
        _point_active_daemon_log(config.life_dir, Path(_hlog))

    rc = 2
    try:
        rc = int(worker.run_forever())
    except Exception:  # noqa: BLE001 - a failed candidate must release and roll back
        log.exception("handoff candidate crashed after takeover")
    finally:
        lock.release()
        try:
            status_path.unlink()
        except OSError:
            pass
    rollback_root = os.environ.get(_HANDOFF_ROLLBACK_SOURCE_ENV, "").strip()
    if rc != 0 and rollback_root:
        root = Path(rollback_root).expanduser().resolve()
        if not _spawn_handoff_candidate(
            config,
            reason="handoff candidate failed; restore prior runtime",
            candidate_source_root=root,
        ):
            _record_handoff_alert(
                config,
                "The handed-off runtime failed after taking over and the prior "
                "runtime did not restart in its place: no daemon is running for "
                "this project. Start it again with `argus --daemon`.",
                reason="daemon_handoff_rollback_failed",
            )
    return rc


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

__all__ = [
    "_HANDOFF_CONFIG_ENV",
    "_HANDOFF_INCUMBENT_PID_ENV",
    "_HANDOFF_LOG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_ROLLBACK_SOURCE_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_consume_deployment_handoff",
    "_acquire_daemon_lock_with_timeout",
    "_record_handoff_alert",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "_truthy_env",
    "request_deployment_handoff",
    "run_handoff_child_process",
]
