"""Manager-handoff identity persistence helpers
for the 7x24 daemon worker.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. These are self-contained pure functions
(no dependency on ``LifeWorker`` or any of its lifecycle-phase mixins) used
by the boot-phase mixin in ``_life_worker_boot.py``.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - detached daemon is POSIX-only
    _fcntl = None

from ..core.atomic_replace import write_atomic
from .config import LifeWorkerConfig
from .state import (
    RESUMABLE_STOP_REASONS,
    ContinuousConfigState,
    read_continuous_state,
    write_continuous_config,
)

log = logging.getLogger(__name__)

_MANAGER_HANDOFF_IDENTITY_FILE = "manager-handoff.json"


def _manager_handoff_identity_path(runtime_root: Path) -> Path:
    return runtime_root / _MANAGER_HANDOFF_IDENTITY_FILE


def _write_manager_handoff_identity(
    runtime_root: Path,
    *,
    objective: str,
    vertical: str,
    domain: str,
    continuous_generation: int,
    intent_id: str,
    source_objective: str = "",
    source_objective_path: str = "",
) -> bool:
    path = _manager_handoff_identity_path(runtime_root)
    payload = {
        "version": 4,
        "objective": str(objective).strip(),
        "vertical": str(vertical).strip(),
        "domain": str(domain).strip(),
        "continuous_generation": max(0, int(continuous_generation)),
        "intent_id": str(intent_id),
        "recorded_at": time.time(),
    }
    source_path = str(source_objective_path or "").strip()
    if source_path:
        payload["source_objective_path"] = str(
            Path(source_path).expanduser().resolve()
        )
        payload["source_objective"] = str(source_objective).strip()
    try:
        write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return True
    except OSError:
        log.exception("failed to persist Manager handoff identity: %s", path)
        return False


def _read_manager_handoff_identity(runtime_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            _manager_handoff_identity_path(runtime_root).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    # Earlier versions named the objective only by a digest, which reads as no identity.
    if not isinstance(payload, dict) or payload.get("version") != 4:
        return None
    return payload


def _refresh_file_backed_objective_for_resume(
    *,
    cfg: LifeWorkerConfig,
    runtime_root: Path,
    state: ContinuousConfigState,
) -> bool:
    """Promote a changed file-backed objective into a fresh Manager handoff.

    A normal ``--resume-continuous`` launch reuses the Manager-clean execution
    objective persisted in ``continuous.json``. When that handoff records the
    operator's original ``--objective-file``, compare the current file before
    adopting it. A changed file becomes an explicit replacement objective;
    unchanged or stale metadata keeps the fast crash-recovery path.
    """
    if (
        getattr(cfg, "continuous", False)
        or not getattr(cfg, "resume_continuous", False)
        or not state.enabled
    ):
        return False
    identity = _read_manager_handoff_identity(runtime_root)
    if not _manager_handoff_identity_matches(
        identity,
        objective=state.objective,
        vertical=str(identity.get("vertical") or "") if identity else "",
        domain=str(identity.get("domain") or "") if identity else "",
        generation=state.generation,
    ):
        return False
    source_path = str((identity or {}).get("source_objective_path") or "").strip()
    recorded = str((identity or {}).get("source_objective") or "").strip()
    if not source_path or not recorded:
        return False
    path = Path(source_path).expanduser()
    try:
        objective = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        log.warning(
            "daemon: file-backed continuous objective is unreadable; "
            "keeping persisted Manager handoff (%s)",
            path,
        )
        return False
    if not objective or objective == recorded:
        return False
    cfg.continuous = True
    cfg.continuous_objective = objective
    cfg.continuous_objective_file = path.resolve()
    log.info(
        "daemon: objective file changed; requesting a fresh Manager handoff (%s)",
        path,
    )
    return True


def _legacy_manager_handoff_identity(
    runtime_root: Path,
    *,
    objective: str,
    vertical: str,
    domain: str,
) -> dict[str, Any] | None:
    """Recover one pre-sidecar Manager handoff from the immutable event tape."""
    handles: list[tuple[float, Any]] = []
    lock_path = runtime_root / "events.lock"
    lock_handle = lock_path.open("a+b")
    try:
        if _fcntl is not None:
            _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_SH)
        for path in runtime_root.glob("events.jsonl*"):
            try:
                handle = path.open("rb")
                metadata = os.fstat(handle.fileno())
            except OSError:
                continue
            handles.append((float(metadata.st_mtime), handle))
    finally:
        if _fcntl is not None:
            _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_UN)
        lock_handle.close()

    def _reverse_lines(handle: Any) -> Iterable[bytes]:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(64 * 1024, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]
            yield from reversed(parts[1:])
        if remainder:
            yield remainder

    try:
        for _mtime, handle in sorted(handles, key=lambda row: row[0], reverse=True):
            lines = _reverse_lines(handle)
            for raw_bytes in lines:
                raw = raw_bytes.decode("utf-8", errors="replace")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "life.manager.intent.completed":
                    continue
                if str(event.get("execution_task") or "").strip() != objective.strip():
                    continue
                if str(event.get("vertical") or "").strip() != vertical.strip():
                    continue
                if str(event.get("domain") or "").strip() != domain.strip():
                    continue
                return {
                    "version": 4,
                    "objective": objective.strip(),
                    "vertical": vertical,
                    "domain": domain,
                    "continuous_generation": max(
                        0,
                        int(event.get("continuous_generation") or 0),
                    ),
                    "intent_id": str(event.get("intent_id") or "legacy-event"),
                }
        return None
    finally:
        for _mtime, handle in handles:
            if not handle.closed:
                handle.close()


def _manager_handoff_identity_matches(
    identity: dict[str, Any] | None,
    *,
    objective: str,
    vertical: str,
    domain: str,
    generation: int,
) -> bool:
    if identity is None:
        return False
    return (
        identity.get("objective") == str(objective).strip()
        and str(identity.get("vertical") or "") == vertical
        and str(identity.get("domain") or "") == domain
        and int(identity.get("continuous_generation") or 0) <= generation
    )


def _daemon_objective_requires_stage_reset(
    *,
    project_root: Path,
    prior_vertical: str,
    next_vertical: str,
    prior_domain: str = "",
    next_domain: str = "",
    prior_handoff: dict[str, Any] | None,
    expected_objective: str,
    source_objective: str,
    execution_task: str,
) -> bool:
    """Return whether a daemon-boot Manager handoff must reopen the pipeline.

    A completed workspace can outlive its old Argus state root. In that case
    there may be no prior Manager identity to compare, but treating the
    terminal stage as current would make every new objective complete without
    executing. A real Manager boot handoff therefore reopens a terminal
    pipeline even when the old identity sidecar is absent.
    """
    from ..manager.front_door import objective_update_requires_stage_reset
    from ..skills.vertical_select import vertical_reached_own_terminal_stage

    prior_vertical = str(prior_vertical or "").strip()
    next_vertical = str(next_vertical or "").strip()
    if prior_vertical:
        try:
            if vertical_reached_own_terminal_stage(
                project_root,
                prior_vertical,
            ):
                return True
        except Exception:  # noqa: BLE001 - fall through to identity comparison
            pass
    if prior_handoff is not None:
        objective_changed = prior_handoff.get("objective") != str(execution_task).strip()
        return bool(
            (
                prior_vertical
                and next_vertical
                and prior_vertical != next_vertical
            )
            or prior_domain != next_domain
            or (
                objective_changed
                and objective_update_requires_stage_reset(
                    expected_objective,
                    source_objective,
                    execution_task,
                )
            )
        )
    return False


def _resume_matches_manager_handoff(
    *,
    cfg: LifeWorkerConfig,
    runtime_root: Path,
    state: ContinuousConfigState,
    objective: str,
) -> bool:
    if not getattr(cfg, "resume_continuous", False) or not state.enabled:
        return False
    if getattr(cfg, "continuous", False):
        return False
    from ..skills.vertical_select import (
        VerticalResolutionError,
        _persisted_domain,
        _persisted_vertical,
    )

    try:
        vertical = _persisted_vertical(runtime_root)
    except VerticalResolutionError:
        return False  # the Manager handoff path reports the unresolvable vertical
    if not vertical:
        return False
    domain = _persisted_domain(runtime_root) or ""
    identity = _read_manager_handoff_identity(runtime_root)
    if not _manager_handoff_identity_matches(
        identity,
        objective=objective,
        vertical=vertical,
        domain=domain,
        generation=state.generation,
    ):
        identity = _legacy_manager_handoff_identity(
            runtime_root,
            objective=objective,
            vertical=vertical,
            domain=domain,
        )
        if identity is not None:
            _write_manager_handoff_identity(
                runtime_root,
                objective=objective,
                vertical=vertical,
                domain=domain,
                continuous_generation=int(
                    identity.get("continuous_generation") or 0
                ),
                intent_id=str(identity.get("intent_id") or "legacy-event"),
            )
    return _manager_handoff_identity_matches(
        identity,
        objective=objective,
        vertical=vertical,
        domain=domain,
        generation=state.generation,
    )


def _rearm_operator_drain_for_resume(
    *,
    cfg: LifeWorkerConfig,
    runtime_root: Path,
    state: ContinuousConfigState,
) -> ContinuousConfigState:
    """Re-arm the temporary state created by an operator stopping the process.

    Draining and SIGTERM both disable continuous mode so the current mission can
    finish without a new one starting.  A supervisor then restarts with
    ``--resume-continuous``; treating the disabled file literally creates an
    alive-but-idle daemon.  Only drain used to be re-armed, so every restart
    onto new code retired the campaign instead: the daemon came back, drained
    its backlog and went quiet forever while still reporting healthy, which is
    how two campaigns spent a day looking alive with nothing running.  Reasons
    that describe the WORK rather than the process -- a planner-declared
    completion, an operator hold -- stay authoritative and are never resumed.
    """
    if (
        not getattr(cfg, "continuous", False)
        and getattr(cfg, "resume_continuous", False)
        and not state.enabled
        and state.done_reason in RESUMABLE_STOP_REASONS
        and state.objective.strip()
    ):
        write_continuous_config(
            runtime_root,
            enabled=True,
            objective=state.objective,
        )
        return read_continuous_state(runtime_root)
    return state


def _effective_runner_backend(runner: Any, configured_backend: str) -> str:
    backend = getattr(runner, "backend", None)
    return str(getattr(backend, "backend", "") or configured_backend)


def _apply_continuous_suppression(
    state: dict,
    enabled: bool,
    objective: str,
    *,
    generation: int | None = None,
) -> tuple[bool, str]:
    """Gate a persisted continuous read against a fresh-daemon suppression.

    A generation-aware caller lifts suppression on every explicit rewrite,
    including re-arming the same objective. ``generation=None`` retains the
    legacy value-based behavior for compatibility callers.
    """
    if state.get("active"):
        same_generation = (
            generation == state.get("generation")
            if generation is not None
            else (objective or "").strip() == state.get("objective", "")
        )
        if enabled and same_generation:
            return False, objective
        state["active"] = False
    return enabled, objective
