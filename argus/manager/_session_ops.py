"""argus.manager._session_ops — session-lock plumbing for the Manager.

Contains every module-level name related to the Manager's persistent codex
session and its two cross-platform advisory file locks:

* ``.manager_session.lock`` — held by the one turn continuing the shared
  thread; other turns never wait for it.
* ``manager_pipeline_lock`` — serialises short Manager commits (stage
  transitions, operator handoffs); never held while a mission runs.

``_restore_files_on_error`` is also here because it guards the same atomic
write contract that the session/pipeline commits rely on.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import replace
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable

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
_LOCK_POLL_SECONDS = 0.2


class ManagerLockCancelled(RuntimeError):
    """A caller cancelled its wait before entering the protected boundary."""

    phase = "cancelled"


def _lock_is_contended(exc: BaseException) -> bool:
    """Recognize contention without retrying bad descriptors or broken locking.

    Portalocker 3/4 may wrap the OS exception in args or in __cause__.
    AlreadyLocked is the portable indication, including Windows lock violation.
    """
    pending = [exc]
    seen: set[int] = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if isinstance(error, portalocker.exceptions.AlreadyLocked):
            return True
        if isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EAGAIN}:
            return True
        pending.extend(arg for arg in error.args if isinstance(arg, BaseException))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
    return False


def _check_lock_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise ManagerLockCancelled("Manager pipeline lock wait cancelled")


def _acquire_session_lock(
    fh: Any, *, timeout: float | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Acquire ``LOCK_EX``; ``False`` when it is still held at ``timeout``.

    ``timeout=None`` waits for real contention. Cancellation and locking
    failures propagate.
    """
    deadline = (
        time.monotonic() + max(0.0, timeout)
        if timeout is not None
        else None
    )
    while True:
        _check_lock_cancelled(cancelled)
        try:
            portalocker.lock(
                fh,
                portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            return True
        except (OSError, portalocker.exceptions.LockException) as exc:
            if not _lock_is_contended(exc):
                raise
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(_LOCK_POLL_SECONDS)


class _PipelineLockState:
    """Per-lock-file in-process state backing ``manager_pipeline_lock``.

    ``gate`` serialises the re-entrant acquisitions made by threads on the
    flock holder's delegation chain (see ``_pipeline_lock_delegation``).
    """

    __slots__ = ("gate",)

    def __init__(self) -> None:
        self.gate = threading.RLock()


# Keyed by the resolved lock-file path so every spelling of the same root
# shares one state. Guarded by ``_pipeline_lock_registry_mutex``.
_pipeline_lock_registry: dict[str, _PipelineLockState] = {}
_pipeline_lock_registry_mutex = threading.Lock()

# The DELEGATION CHAIN: the set of resolved pipeline-lock paths whose flock
# the current context's chain of custody holds. Set by the top-level flock
# acquirer; visible to same-thread nested acquisitions automatically, and to
# worker threads ONLY when the holder explicitly hands its context over
# (``contextvars.copy_context().run``). A plain ``threading.Thread`` starts with a
# fresh context, so independent threads (telegram/feishu pollers, concurrent
# webapi requests) never inherit re-entry entitlement.
_pipeline_lock_delegation: ContextVar[frozenset[str]] = ContextVar(
    "argus_pipeline_lock_delegation",
    default=frozenset(),
)


@contextmanager
def manager_pipeline_lock(
    root: Path | str, *, cancelled: Callable[[], bool] | None = None,
):
    """Serialize short Manager commits: stage transitions and operator handoffs.

    Cross-process: an exclusive advisory flock on ``<root>/.manager_pipeline.lock``.
    Nobody holds it while a mission runs, so an operator handoff never waits
    for running work. In-process: re-entrant only along the holder's chain of
    custody, tracked by a ``ContextVar`` set of held lock paths; a nested
    acquisition whose context carries this path skips the flock (a second
    file handle would deadlock: POSIX flock is exclusive across open file
    descriptions even within one process) and serialises on a per-lock-file
    ``threading.RLock`` gate instead. Threads with a fresh context take the
    flock and wait.

    Cancellation only abandons acquisition; it never revokes an acquired lock.
    """
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    lock_path = path / _PIPELINE_LOCK
    key = str(lock_path.resolve())
    with _pipeline_lock_registry_mutex:
        state = _pipeline_lock_registry.setdefault(key, _PipelineLockState())
    if key in _pipeline_lock_delegation.get():
        while True:
            _check_lock_cancelled(cancelled)
            if state.gate.acquire(blocking=False):
                break
            time.sleep(0.05)
        try:
            _check_lock_cancelled(cancelled)
            yield
        finally:
            state.gate.release()
        return
    with lock_path.open("a+b") as handle:
        _acquire_session_lock(handle, cancelled=cancelled)
        try:
            _check_lock_cancelled(cancelled)
            # Grant the entitlement only after the flock is ours, inside the
            # try: if anything below raises, reset() runs before unlock and
            # no context is left with an orphaned entitlement.
            token = _pipeline_lock_delegation.set(
                _pipeline_lock_delegation.get() | {key}
            )
            try:
                _check_lock_cancelled(cancelled)
                yield
            finally:
                _pipeline_lock_delegation.reset(token)
        finally:
            portalocker.unlock(handle)


@contextmanager
def manager_pipeline_boundary(
    manager: Any, *, cancelled: Callable[[], bool] | None = None,
):
    """Enter a Manager's boundary, adapting legacy no-argument lock factories.

    Production Manager forwards cancellation into lock acquisition. Structural
    substitutes may only implement ``pipeline_lock()``; inspect that capability
    before calling, rather than mistaking an internal TypeError for a mismatch.
    Legacy factories still get cancellation checks before and after acquisition.
    """
    _check_lock_cancelled(cancelled)
    factory = getattr(manager, "pipeline_lock", None)
    kwargs: dict[str, Any] = {}
    if callable(factory):
        try:
            parameters = tuple(signature(factory).parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        if cancelled is not None and any(
            parameter.kind == Parameter.VAR_KEYWORD
            or (parameter.name == "cancelled" and parameter.kind != Parameter.POSITIONAL_ONLY)
            for parameter in parameters
        ):
            kwargs["cancelled"] = cancelled
    with factory(**kwargs) if callable(factory) else nullcontext():
        _check_lock_cancelled(cancelled)
        yield


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
    """A file-lock-guarded persistent codex session shared by every Manager LLM
    call. The thread_id lives at ``<project_root>/.manager_session.json``; a
    sibling ``.manager_session.lock`` is held by the one turn continuing that
    thread, so the cockpit front-end and the daemon never interleave on it.
    Fail-open: a turn that finds the thread busy, or any lock/IO error, makes a
    call on a fresh thread instead — the Manager's decision must never wait on
    this. Once a provider call starts, unexpected failures are propagated;
    repeating an uncertain call could repeat tool effects.

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
    def _read_state(self) -> dict[str, Any]:
        try:
            with self._session_path.open("rb") as handle:
                raw = handle.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                return {}
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 — missing/corrupt/unreadable → no session
            return {}

    def _read_tid(self) -> str | None:
        tid = self._read_state().get("thread_id")
        return tid.strip() or None if isinstance(tid, str) else None

    def _write_tid(self, tid: str, *, state: dict[str, Any] | None = None) -> None:
        # Atomic replace so a concurrent reader never sees a half-written file.
        self.project_root.mkdir(parents=True, exist_ok=True)
        tmp = self._session_path.with_suffix(
            self._session_path.suffix + f".tmp.{os.getpid()}"
        )
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump({**(state or {}), "thread_id": tid}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._session_path)
            from ..daemon.state import _fsync_directory

            _fsync_directory(self.project_root)
        finally:
            tmp.unlink(missing_ok=True)

    @property
    def backend(self) -> str:
        """The agent CLI behind the session, which decides how its Argus actions are bound."""
        return getattr(self.runner, "backend", "")

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

        The session lock is held only by the turn continuing the shared thread
        and is released by the OS if its owner exits. A turn that finds it held
        does not wait: it answers at once on a fresh thread that carries the
        saved recent turns, leaving the shared thread to its holder.

        Session setup failures can fall back to one plain call. Unexpected
        errors after provider dispatch are never replayed. A specifically
        rejected resume target can still rotate through the explicit branch
        below, which carries the bounded saved conversation handoff.
        """
        from ..core.file_lock import FileLockCancelled, bounded_file_lock_wait
        from ..core.operator_context import build_operator_context_block
        from ..core.run_gateway import current_run_interrupt_reason
        from .session_continuity import ManagerSessionContinuityUnavailable

        original_prompt = prompt
        interruption = getattr(options, "external_interrupt_reason_provider", None)

        def _cancelled() -> bool:
            return bool((callable(interruption) and interruption()) or current_run_interrupt_reason())

        def _current_prompt() -> str:
            try:
                with bounded_file_lock_wait(timeout_seconds=0.25, cancelled=_cancelled):
                    operator_context, _revision = build_operator_context_block(
                        "manager", self.project_root, consume_once=False
                    )
            except (OSError, FileLockCancelled) as exc:
                _check_lock_cancelled(_cancelled)
                raise ManagerSessionContinuityUnavailable(
                    "Current Manager permissions could not be read; provider was not started"
                ) from exc
            if operator_context:
                from ..core.operator_context import append_operator_context

                return append_operator_context(original_prompt, operator_context)
            return original_prompt
        # Per-turn discovery (including an explicit empty set for lean chat)
        # must survive the persistent session's cached defaults.
        if self.skill_paths and options.skill_paths is None:
            options = replace(options, skill_paths=list(self.skill_paths))

        def _no_session() -> Any:
            return gateway_run_exec(
                self.runner,
                prompt=_current_prompt(), options=options, run_label=run_label
            )

        try:
            self.project_root.mkdir(parents=True, exist_ok=True)
            fh = self._lock_path.open("a+b")
        except Exception:  # noqa: BLE001 — lock setup failed → no-session fail-open
            return _no_session()

        from .session_context import remember_turn, session_handoff, session_identity

        provider_attempted = False
        try:
            if not _acquire_session_lock(fh, timeout=0, cancelled=_cancelled):
                call_prompt = session_handoff(
                    self._read_state(), _current_prompt(), "another Manager turn holds the shared thread",
                )
                provider_attempted = True
                return gateway_run_exec(
                    self.runner, prompt=call_prompt, options=options, run_label=run_label,
                )
            try:
                prior = self._read_state()
                tid = self._read_tid()
                prior_tid = tid
                # Permission may have changed while this turn waited for the
                # shared provider session. Project it immediately before use.
                prompt = _current_prompt()
                if options is not None and not getattr(options, "model", ""):
                    prior_model = (prior.get("identity") or {}).get("model", "")
                    if prior_model:
                        options = replace(options, model=prior_model)
                identity = session_identity(self.runner, options)
                call_prompt = prompt
                rotation_reason = (
                    "the configured model, backend or handoff protocol changed"
                    if tid and prior.get("identity") not in (None, identity) else ""
                )
                if rotation_reason:
                    call_prompt = session_handoff(
                        prior, prompt, rotation_reason, project_root=self.project_root, run_label=run_label,
                        cancelled=_cancelled,
                    )
                    tid = None
                continued = bool(tid)
                provider_attempted = True
                result = gateway_run_exec(
                    self.runner,
                    prompt=call_prompt,
                    options=options,
                    run_label=run_label,
                    resume_thread_id=tid,
                    # A lost session is rotated below with a handoff.
                    fresh_on_missing_resume=False,
                )
                failed, _detail = _manager_backend_failure(result)
                if tid and failed and result_has_unrecoverable_resume_state(result):
                    log.warning(
                        "Manager persistent session %s is unrecoverable; "
                        "rotating to a fresh thread",
                        tid,
                    )
                    _check_lock_cancelled(_cancelled)
                    rotation_reason = "the previous provider thread is no longer resumable"
                    prompt = _current_prompt()
                    call_prompt = session_handoff(
                        prior, prompt, rotation_reason, project_root=self.project_root, run_label=run_label,
                        cancelled=_cancelled,
                    )
                    continued = False
                    result = gateway_run_exec(
                        self.runner,
                        prompt=call_prompt,
                        options=options,
                        run_label=run_label,
                    )
                new = getattr(result, "thread_id", None)
                if new and not _cancelled():
                    try:
                        logical_id = prior.get("logical_manager_id")
                        if not isinstance(logical_id, str) or not 0 < len(logical_id) <= 128:
                            logical_id = "manager-" + uuid.uuid4().hex
                        generation = prior.get("provider_generation", 1 if prior_tid else 0)
                        if type(generation) is not int or generation < 0:
                            generation = 0
                        generation += not continued
                        last_rotation = prior.get("last_rotation")
                        if rotation_reason:
                            last_rotation = {"reason": rotation_reason, "from_thread_id": prior_tid,
                                             "to_thread_id": str(new), "provider_generation": generation}
                        self._write_tid(str(new), state={
                            "version": 3, "identity": identity,
                            "logical_manager_id": logical_id,
                            "provider_generation": generation,
                            "recent_turns": remember_turn(prior, prompt, result, run_label),
                            "rotation_reason": rotation_reason,
                            "last_rotation": last_rotation,
                        })
                    except Exception:  # noqa: BLE001 — persist is best-effort
                        pass
                return result
            finally:
                try:
                    portalocker.unlock(fh)
                except Exception:  # noqa: BLE001
                    pass
        except ManagerSessionContinuityUnavailable:
            _check_lock_cancelled(_cancelled)
            raise
        except (BackendLoginRequired, ManagerLockCancelled):
            raise
        except Exception:  # noqa: BLE001 — only pre-dispatch session setup may degrade
            if provider_attempted:
                raise
            return _no_session()
        finally:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def reset_manager_session(project_root: Path | str) -> bool:
    """Drop the Manager's persistent codex session pointer at ``project_root``.

    This is an explicit conversation reset. Ordinary daemon/frontend restarts
    preserve the identity and resume it. Stage authority remains in the durable
    pipeline state; resetting this pointer does not change team controls.

    Best-effort, never raises. Returns True if a session pointer existed.
    """
    session_path = Path(project_root) / _SESSION_FILE
    try:
        existed = session_path.exists()
        session_path.unlink(missing_ok=True)
        return existed
    except Exception:  # noqa: BLE001 — best-effort; never block boot / 尽力而为，不阻塞启动
        return False
