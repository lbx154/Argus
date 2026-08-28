"""argus.manager._session_ops — session-lock plumbing for the Manager.

Contains every module-level name related to the Manager's persistent codex
session and its two cross-platform advisory file locks:

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
from dataclasses import replace
from pathlib import Path
from typing import Any

import portalocker

from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.runner_errors import result_has_unrecoverable_resume_state
from ..provider_integrations.authorization_retry import BackendLoginRequired
from ._helpers import _manager_backend_failure

log = logging.getLogger(__name__)

# Where the Manager's one persistent codex session lives (under project_root).
_SESSION_FILE = ".manager_session.json"
_SESSION_LOCK = ".manager_session.lock"
_PIPELINE_LOCK = ".manager_pipeline.lock"
_PIPELINE_YIELD_FILE = ".manager_pipeline_yield.json"


def _acquire_session_lock(fh: Any, *, timeout: float | None = None) -> bool:
    """Acquire ``LOCK_EX``, optionally bounded for explicit diagnostic callers.

    Production Manager locks wait until the OS releases the peer's lock.
    """
    deadline = (
        time.monotonic() + max(0.0, timeout)
        if timeout is not None
        else None
    )
    while True:
        try:
            portalocker.lock(
                fh,
                portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            return True
        except (OSError, portalocker.exceptions.LockException):
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


@contextmanager
def manager_pipeline_lock(root: Path | str):
    """Serialize Manager pipeline commits with daemon mission execution."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    with (path / _PIPELINE_LOCK).open("a+b") as handle:
        _acquire_session_lock(handle)
        try:
            yield
        finally:
            portalocker.unlock(handle)


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
    except (OSError, json.JSONDecodeError):
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
    except (OSError, TypeError, ValueError):
        return False
    if not token or pid <= 0:
        _clear_pipeline_yield_if_token(path, token)
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        _clear_pipeline_yield_if_token(path, token)
        return False
    return True


@contextmanager
def manager_session_lock(root: Path | str):
    """Wait until no Manager LLM turn is using this session's workdir."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    with (path / _SESSION_LOCK).open("a+b") as handle:
        _acquire_session_lock(handle)
        try:
            yield
        finally:
            portalocker.unlock(handle)


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
    """A file-lock-serialized persistent codex session shared by every Manager LLM
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
        self.skill_paths: list[str] = []

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
        """Run one turn on the shared persistent session under an advisory lock.

        The session lock serializes the cockpit and daemon's shared Manager
        thread. It is released by the OS if its owner exits.

        Fail-open recovery: if anything in the session-mode path fails (lock setup,
        a corrupt resume tid, a runner that does not accept ``resume_thread_id``),
        we fall back to ONE plain no-session call — a deliberate recovery + runner
        compatibility shim. The fallback runs AFTER the lock is released, never
        nested under it.
        """
        from ..core.operator_context import build_operator_context_block

        try:
            operator_context, _operator_context_revision = build_operator_context_block(
                "manager", self.project_root, consume_once=False
            )
        except OSError:
            operator_context = ""
        if operator_context:
            from ..core.operator_context import append_operator_context

            prompt = append_operator_context(prompt, operator_context)
        if self.skill_paths:
            options = replace(options, skill_paths=list(self.skill_paths))

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
            _acquire_session_lock(fh)
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
                try:
                    portalocker.unlock(fh)
                except Exception:  # noqa: BLE001
                    pass
        except BackendLoginRequired:
            raise
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
    ``.argus/PIPELINE_STATE.json``, so dropping the thread_id pointer loses
    nothing load-bearing; the on-disk codex transcript stays auditable.
    中文：新 daemon 是全新的隔离代际，绝不能 resume 上一个 daemon 的 Manager
    会话（它会跨代际无界增长，直到 codex 有损压缩）。stage 真相在
    ``.argus/PIPELINE_STATE.json`` 里，清掉 thread_id 指针不丢任何承重信息；
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
