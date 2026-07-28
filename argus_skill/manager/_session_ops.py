"""argus.manager._session_ops — session-lock plumbing for the Manager.

Contains every module-level name related to the Manager's persistent codex
session and its two POSIX advisory file locks:

* ``manager_session_lock`` — serialises concurrent Manager LLM turns.
* ``manager_pipeline_lock`` — serialises Manager commits with daemon mission
  execution (the "pipeline boundary yield" handshake).

``_restore_files_on_error`` is also here because it guards the same atomic
write contract that the session/pipeline commits rely on.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:  # POSIX advisory file locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.runner_errors import result_has_unrecoverable_resume_state
from ._helpers import _manager_backend_failure

log = logging.getLogger(__name__)

# Where the Manager's one persistent codex session lives (under project_root).
_SESSION_FILE = ".manager_session.json"
_SESSION_LOCK = ".manager_session.lock"
_PIPELINE_LOCK = ".manager_pipeline.lock"
_PIPELINE_YIELD_FILE = ".manager_pipeline_yield.json"


def _session_lock_timeout_s() -> float:
    """Bounded wait for the shared Manager session lock (default 120s). Manager
    turns are short LLM calls (classify / stage / skill-review), so 120s easily
    covers a normal turn while capping starvation if a peer turn hangs."""
    raw = os.environ.get("ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else 120.0
    except ValueError:
        return 120.0


def _pipeline_lock_timeout_s() -> float:
    raw = os.environ.get("ARGUS_SKILL_MANAGER_PIPELINE_LOCK_TIMEOUT_S", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else 1800.0
    except ValueError:
        return 1800.0


def _acquire_session_lock(fh: Any, *, timeout: float) -> bool:
    """Acquire ``LOCK_EX`` non-blocking, retrying up to ``timeout`` seconds.

    Returns True if acquired, False if the peer held it past the budget (a
    long/hung turn) — so the caller can fail-open instead of blocking forever.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


@contextmanager
def manager_pipeline_lock(root: Path | str):
    """Serialize Manager pipeline commits with daemon mission execution."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    with (path / _PIPELINE_LOCK).open("a+b") as handle:
        if fcntl is not None and not _acquire_session_lock(
            handle,
            timeout=_pipeline_lock_timeout_s(),
        ):
            raise TimeoutError("timed out waiting for the current mission boundary")
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def request_manager_pipeline_yield(root: Path | str) -> str:
    """Ask the daemon to leave the next mission boundary open for Manager."""
    path = Path(root) / _PIPELINE_YIELD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "schema_version": 1,
        "token": token,
        "pid": os.getpid(),
        "requested_at": time.time(),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{token}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return token


def _clear_pipeline_yield_if_token(path: Path, token: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or str(payload.get("token") or "") != token:
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def clear_manager_pipeline_yield(root: Path | str, token: str) -> bool:
    return _clear_pipeline_yield_if_token(
        Path(root) / _PIPELINE_YIELD_FILE,
        token,
    )


def manager_pipeline_yield_requested(root: Path | str) -> bool:
    """Return whether a live Manager request is waiting for the boundary."""
    path = Path(root) / _PIPELINE_YIELD_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        token = str(payload.get("token") or "")
        pid = int(payload.get("pid") or 0)
        requested_at = float(payload.get("requested_at") or 0.0)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not token or pid <= 0:
        _clear_pipeline_yield_if_token(path, token)
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        _clear_pipeline_yield_if_token(path, token)
        return False
    if requested_at <= 0 or time.time() - requested_at > _pipeline_lock_timeout_s() + 60:
        _clear_pipeline_yield_if_token(path, token)
        return False
    return True


@contextmanager
def manager_session_lock(root: Path | str):
    """Wait until no Manager LLM turn is using this session's workdir."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    with (path / _SESSION_LOCK).open("a+b") as handle:
        if fcntl is not None and not _acquire_session_lock(
            handle,
            timeout=_session_lock_timeout_s(),
        ):
            raise TimeoutError("timed out waiting for the current Manager turn")
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _restore_files_on_error(paths: list[Path]):
    snapshots: dict[Path, bytes | None] = {}
    for path in paths:
        try:
            snapshots[path] = path.read_bytes()
        except FileNotFoundError:
            snapshots[path] = None
    try:
        yield
    except Exception:
        for path, content in snapshots.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.rollback.{os.getpid()}")
                tmp.write_bytes(content)
                os.replace(tmp, path)
            except OSError:
                log.exception("failed to restore Manager pipeline artifact %s", path)
        raise


class _ManagerSession:
    """A flock-serialized, persistent codex session shared by every Manager LLM
    call. The thread_id lives at ``<project_root>/.manager_session.json``; a
    sibling ``.manager_session.lock`` serializes cross-process use so the cockpit
    front-end and the daemon never interleave a turn. Fail-open: any lock/IO
    error degrades to a plain no-session call — the Manager's decision must never
    be blocked by this.

    This is a "runner-like" wrapper: it exposes ``run_exec(prompt=, options=,
    run_label=)`` so it can be passed anywhere a runner is expected
    (``classify_vertical`` and other Manager calls). It IGNORES any incoming
    ``resume_thread_id`` and always continues the persistent session instead.
    """

    def __init__(self, runner: Any, project_root: Path | str) -> None:
        self.runner = runner
        self.project_root = Path(project_root)
        self._session_path = self.project_root / _SESSION_FILE
        self._lock_path = self.project_root / _SESSION_LOCK

    # --- persistent thread_id IO (corrupt/missing → None, never raises) ---
    def _read_tid(self) -> str | None:
        try:
            data = json.loads(self._session_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            tid = data.get("thread_id")
            if not isinstance(tid, str):
                return None
            tid = tid.strip()
            return tid or None
        except Exception:  # noqa: BLE001 — missing/corrupt/unreadable → no session
            return None

    def _write_tid(self, tid: str) -> None:
        # Atomic replace so a concurrent reader never sees a half-written file.
        self.project_root.mkdir(parents=True, exist_ok=True)
        tmp = self._session_path.with_suffix(
            self._session_path.suffix + f".tmp.{os.getpid()}"
        )
        tmp.write_text(json.dumps({"thread_id": tid}), encoding="utf-8")
        os.replace(tmp, self._session_path)

    @property
    def thread_id(self) -> str | None:
        """The current persistent session thread_id (for tests / future
        chat-reply wiring); ``None`` when no session has been established."""
        return self._read_tid()

    # --- the runner-like surface ---
    def run_exec(
        self,
        *,
        prompt: str,
        options: Any,
        run_label: str,
        resume_thread_id: str | None = None,  # noqa: ARG002 — runner Protocol parity; ignored
    ) -> Any:
        """Run one turn on the shared persistent session, serialized by flock.

        The session lock is acquired NON-blocking with a bounded wait
        (``ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S``, default 120s), so a long/hung turn
        in the peer process (cockpit vs daemon share one lock per cwd) can't freeze
        this one indefinitely — if it can't be acquired in time we fall open to a
        plain no-session call.

        Fail-open recovery: if anything in the session-mode path fails (lock setup,
        a corrupt resume tid, a runner that does not accept ``resume_thread_id``),
        we fall back to ONE plain no-session call — a deliberate recovery + runner
        compatibility shim. The fallback runs AFTER the lock is released, never
        nested under it.
        """
        def _no_session() -> Any:
            return gateway_run_exec(
                self.runner,
                prompt=prompt, options=options, run_label=run_label
            )

        try:
            self.project_root.mkdir(parents=True, exist_ok=True)
            fh = self._lock_path.open("a+b")
        except Exception:  # noqa: BLE001 — lock setup failed → no-session fail-open
            return _no_session()

        try:
            if fcntl is not None and not _acquire_session_lock(
                fh, timeout=_session_lock_timeout_s()
            ):
                # Peer holds a long/hung turn past the budget → don't block forever;
                # a no-session call uses a fresh thread, so it can't corrupt the
                # shared session.
                return _no_session()
            try:
                tid = self._read_tid()
                result = gateway_run_exec(
                    self.runner,
                    prompt=prompt,
                    options=options,
                    run_label=run_label,
                    resume_thread_id=tid,
                )
                failed, _detail = _manager_backend_failure(result)
                if tid and failed and result_has_unrecoverable_resume_state(result):
                    log.warning(
                        "Manager persistent session %s is unrecoverable; "
                        "rotating to a fresh thread",
                        tid,
                    )
                    try:
                        self._session_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    result = gateway_run_exec(
                        self.runner,
                        prompt=prompt,
                        options=options,
                        run_label=run_label,
                    )
                new = getattr(result, "thread_id", None)
                if new:
                    try:
                        self._write_tid(str(new))
                    except Exception:  # noqa: BLE001 — persist is best-effort
                        pass
                return result
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 — session-mode failed (lock released) → no-session
            return _no_session()
        finally:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def reset_manager_session(project_root: Path | str) -> bool:
    """Drop the Manager's persistent codex session pointer at ``project_root``.

    EN: A new daemon is a fresh isolation generation — it must NOT resume the
    prior daemon's Manager conversation, which otherwise grows unbounded across
    generations until codex auto-compaction. Stage truth lives in
    ``research/PIPELINE_STATE.json``, so dropping the thread_id pointer loses
    nothing load-bearing; the on-disk codex transcript stays auditable.
    中文：新 daemon 是全新的隔离代际，绝不能 resume 上一个 daemon 的 Manager
    会话（它会跨代际无界增长，直到 codex 有损压缩）。stage 真相在
    ``research/PIPELINE_STATE.json`` 里，清掉 thread_id 指针不丢任何承重信息；
    盘上的 codex transcript 不动，仍可审计。

    Best-effort, never raises (boot must not be blocked). Returns True if a
    session pointer existed. / 尽力而为、绝不抛异常（不能阻塞 daemon 启动）；
    原本存在会话指针时返回 True。
    """
    session_path = Path(project_root) / _SESSION_FILE
    try:
        existed = session_path.exists()
        session_path.unlink(missing_ok=True)
        return existed
    except Exception:  # noqa: BLE001 — best-effort; never block boot / 尽力而为，不阻塞启动
        return False
