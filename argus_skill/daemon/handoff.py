"""Blue/green daemon handoff and source-change detection."""

from __future__ import annotations

import hashlib
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

from ..core.daemon_lock import DaemonAlreadyRunning, acquire_global_daemon_lock
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
)

log = logging.getLogger(__name__)

_HANDOFF_CONFIG_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG"
_HANDOFF_READY_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_READY"
_HANDOFF_TOKEN_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN"
_HANDOFF_GEN_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_GEN"
_HANDOFF_LOG_ENV = "ARGUS_SKILL_DAEMON_HANDOFF_LOG"
_SOURCE_SIGNATURE_ENV = "ARGUS_SKILL_DAEMON_SOURCE_SIGNATURE"
_TEST_SOURCE_SIGNATURE_FILE_ENV = "ARGUS_SKILL_DAEMON_TEST_SOURCE_SIGNATURE_FILE"


# ---------------------------------------------------------------------------
# Blue/green self-handoff
# ---------------------------------------------------------------------------

def _truthy_env(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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


def _auto_handoff_enabled() -> bool:
    return _truthy_env("ARGUS_SKILL_DAEMON_AUTO_RESTART", "0")


def _handoff_min_interval_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("ARGUS_SKILL_DAEMON_HANDOFF_MIN_S", "60")))
    except ValueError:
        return 60.0


def _handoff_generation() -> int:
    try:
        return max(0, int(os.environ.get(_HANDOFF_GEN_ENV, "0")))
    except ValueError:
        return 0


def _handoff_max_generations() -> int:
    try:
        return max(1, int(os.environ.get("ARGUS_SKILL_DAEMON_HANDOFF_MAX_GEN", "10")))
    except ValueError:
        return 10


def _source_signature() -> str:
    """Content hash of runtime files that require a daemon restart.

    Covers both Python runtime code and the behavior-defining built-in skill
    markdown: a skill edit changes how the engineer/reviewer act, so the daemon
    is just as stale against a skill change as against a ``.py`` change and the
    auto-handoff signature must notice it.
    """
    test_signature_path = os.environ.get(_TEST_SOURCE_SIGNATURE_FILE_ENV, "").strip()
    if test_signature_path:
        try:
            return Path(test_signature_path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parent
    paths: list[Path] = sorted(package_root.rglob("*.py"))
    paths += sorted((package_root / "builtin_skills").rglob("*.md"))
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        paths.append(pyproject)
    digest = hashlib.sha256()
    for path in paths:
        parts = set(path.parts)
        if "__pycache__" in parts or ".git" in parts:
            continue
        try:
            rel = path.relative_to(repo_root)
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(str(rel).encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _handoff_ready_path(life_dir: Path) -> Path:
    return life_dir / "daemon.handoff.json"


def _handoff_config_path(life_dir: Path, token: str) -> Path:
    return life_dir / f"daemon.handoff.{token}.json"


def _spawn_handoff_candidate(
    config: LifeWorkerConfig,
    *,
    source_signature: str,
    reason: str,
    standby_timeout: float = 30.0,
) -> bool:
    """Start a fresh interpreter and wait until it reaches standby."""
    token = uuid.uuid4().hex
    config.life_dir.mkdir(parents=True, exist_ok=True)
    ready_path = _handoff_ready_path(config.life_dir)
    config_path = _handoff_config_path(config.life_dir, token)
    ready_path.unlink(missing_ok=True)
    payload = {
        "token": token,
        "reason": reason,
        "source_signature": source_signature,
        "config": _config_payload(config),
    }
    try:
        config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("daemon handoff: failed to write config")
        return False
    boot_id = _new_boot_id()
    log_path = _daemon_log_path(config.life_dir, config.log_path, boot_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env[_HANDOFF_CONFIG_ENV] = str(config_path)
    env[_HANDOFF_READY_ENV] = str(ready_path)
    env[_HANDOFF_TOKEN_ENV] = token
    env[_HANDOFF_LOG_ENV] = str(log_path)
    env[_SOURCE_SIGNATURE_ENV] = source_signature
    env[_HANDOFF_GEN_ENV] = str(_handoff_generation() + 1)
    cmd = [
        sys.executable,
        "-c",
        (
            "from argus_skill.daemon.life_worker import run_handoff_child; "
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
                start_new_session=os.name != "nt",
                close_fds=True,
            )
    except OSError:
        log.exception("daemon handoff: failed to spawn candidate")
        config_path.unlink(missing_ok=True)
        return False

    deadline = time.monotonic() + standby_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.warning("daemon handoff: candidate exited early rc=%s", proc.returncode)
            config_path.unlink(missing_ok=True)
            return False
        try:
            data = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            time.sleep(0.1)
            continue
        if data.get("token") == token and data.get("state") == "standby":
            return True
        time.sleep(0.1)
    log.warning("daemon handoff: candidate did not reach standby in %.1fs", standby_timeout)
    try:
        proc.terminate()
    except OSError:
        pass
    config_path.unlink(missing_ok=True)
    ready_path.unlink(missing_ok=True)
    return False


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
        sys.stderr.write("argus-skill handoff: missing handoff environment\n")
        return 2
    config_path = Path(config_env).expanduser()
    ready_path = Path(ready_env).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        config = _config_from_payload(payload["config"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(f"argus-skill handoff: invalid config: {exc}\n")
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
        sys.stderr.write(f"argus-skill handoff: failed to write standby file: {exc}\n")
        return 2

    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        lock = acquire_lock(pid_path, 60.0)
    except DaemonAlreadyRunning as exc:
        log.error("handoff candidate could not acquire daemon lock (pid=%s)", exc.pid)
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

    try:
        return worker.run_forever()
    finally:
        lock.release()
        try:
            status_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

__all__ = [
    "_HANDOFF_CONFIG_ENV",
    "_HANDOFF_GEN_ENV",
    "_HANDOFF_LOG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_SOURCE_SIGNATURE_ENV",
    "_TEST_SOURCE_SIGNATURE_FILE_ENV",
    "_acquire_daemon_lock_with_timeout",
    "_auto_handoff_enabled",
    "_handoff_generation",
    "_handoff_max_generations",
    "_handoff_min_interval_seconds",
    "_source_signature",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "_truthy_env",
    "run_handoff_child_process",
]
