"""Manager-handoff identity persistence and vault/backend preflight helpers
for the 7x24 daemon worker.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. These are self-contained pure functions
(no dependency on ``LifeWorker`` or any of its lifecycle-phase mixins) used
by the boot-phase mixin in ``_life_worker_boot.py``.
"""
from __future__ import annotations

import hashlib
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

from .config import LifeWorkerConfig
from .state import ContinuousConfigState, read_continuous_state, write_continuous_config

log = logging.getLogger(__name__)

_MANAGER_HANDOFF_IDENTITY_FILE = "manager-handoff.json"


def _manager_handoff_identity_path(runtime_root: Path) -> Path:
    return runtime_root / _MANAGER_HANDOFF_IDENTITY_FILE


def _objective_sha256(objective: str) -> str:
    return hashlib.sha256(str(objective).strip().encode("utf-8")).hexdigest()


def _write_manager_handoff_identity(
    runtime_root: Path,
    *,
    objective: str,
    vertical: str,
    domain: str,
    continuous_generation: int,
    intent_id: str,
) -> bool:
    path = _manager_handoff_identity_path(runtime_root)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    payload = {
        "version": 2,
        "objective_sha256": _objective_sha256(objective),
        "vertical": str(vertical).strip(),
        "domain": str(domain).strip(),
        "continuous_generation": max(0, int(continuous_generation)),
        "intent_id": str(intent_id),
        "recorded_at": time.time(),
    }
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except OSError:
        log.exception("failed to persist Manager handoff identity: %s", path)
        return False
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_manager_handoff_identity(runtime_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            _manager_handoff_identity_path(runtime_root).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        return None
    return payload


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
                    "version": 2,
                    "objective_sha256": _objective_sha256(objective),
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
        identity.get("objective_sha256") == _objective_sha256(objective)
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
        objective_changed = (
            prior_handoff.get("objective_sha256")
            != _objective_sha256(execution_task)
        )
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
    from ..skills.vertical_select import _persisted_domain, _persisted_vertical

    vertical = _persisted_vertical(cfg.project_workdir or runtime_root)
    if not vertical:
        return False
    domain = _persisted_domain(cfg.project_workdir or runtime_root) or ""
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


def _preflight_route_on_codex(route: str) -> bool:
    """Will this preflight route actually run on the codex/Azure backend?

    EN: Uses the SAME canonical resolution as the role runners
    (``core.knobs.resolve_role_backend``: ``ARGUS_SKILL_{ROLE}_BACKEND`` →
    ``ARGUS_SKILL_RUNNER_BACKEND`` → ``ARGUS_SKILL_LIFE_BACKEND`` → persisted
    knob store → codex). A role pinned to copilot/claude/opencode authenticates through
    its OWN CLI (the copilot subscription / claude), NOT the ``model_api`` vault
    — so probing its Azure route is a FALSE gate. Reading the resolver (not raw
    ``os.environ``) is load-bearing: a non-interactive launcher (the web
    autostart, a bare ``tmux`` exec) never sources the operator's ``.bashrc``,
    so a copilot choice that lives only in an interactive-shell export would be
    invisible here and the daemon would wrongly probe — and fail on — the codex
    vault. The persisted ``/backend`` switch is honoured for exactly this case.
    Unknown/typo'd values fall back to codex so the safety probe is preserved.
    中文：与角色 runner 用同一套规范解析（``resolve_role_backend``：角色 env →
    RUNNER_BACKEND → LIFE_BACKEND → 持久化 knob → codex）。读解析后的后端而非裸
    ``os.environ`` 是关键：web/tmux 这类非交互启动器不 source ``.bashrc``，只写在
    交互 shell 里的 copilot 选择在这里就看不见，daemon 会误探并崩在 codex 金库上；
    持久化的 ``/backend`` 切换正是为这种情况兜底。未知值回退 codex 保留安全探测。
    """
    from ..agent_cli.runner_backend import (
        BACKEND_CODEX,
        resolve_available_runner,
    )
    from ..core.knobs import resolve_role_backend

    role = route if route in ("engineer", "reviewer", "planner", "manager", "curator") else ""
    chosen = resolve_role_backend(role)
    if not chosen:
        chosen = BACKEND_CODEX
    role_bin = (
        os.environ.get(f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", "").strip()
        if role
        else ""
    )
    configured = role_bin or os.environ.get("ARGUS_SKILL_RUNNER_BIN", "").strip()
    try:
        effective, _runner_bin = resolve_available_runner(
            chosen,
            configured or None,
        )
        return effective == BACKEND_CODEX
    except Exception:  # noqa: BLE001 — unknown value: keep the safety probe
        return True


def _rearm_operator_drain_for_resume(
    *,
    cfg: LifeWorkerConfig,
    runtime_root: Path,
    state: ContinuousConfigState,
) -> ContinuousConfigState:
    """Re-arm only the temporary state created by ``--daemon-stop --drain``.

    Drain intentionally disables continuous mode so the current mission can
    finish without a new one starting.  A supervisor then restarts with
    ``--resume-continuous``; treating the disabled file literally creates an
    alive-but-idle daemon.  Other disabled reasons remain authoritative and are
    never auto-resumed (operator holds, normal completion, manual stop, etc.).
    """
    if (
        not getattr(cfg, "continuous", False)
        and getattr(cfg, "resume_continuous", False)
        and not state.enabled
        and state.done_reason == "operator drain-stop"
        and state.objective.strip()
    ):
        write_continuous_config(
            runtime_root,
            enabled=True,
            objective=state.objective,
        )
        return read_continuous_state(runtime_root)
    return state


def required_codex_routes(required: Iterable[str] | None = None) -> list[str]:
    """The subset of preflight routes that will hit the codex/Azure model_api.

    Roles routed to copilot/claude/opencode are excluded (they never touch the Azure
    vault). When this returns ``[]`` the daemon can skip the vault preflight
    entirely — e.g. a fully copilot-backed run needs no Azure routes at all.
    返回真正会打到 codex/Azure model_api 的预检路由子集；copilot/claude 的角色被排除。
    返回 ``[]`` 时可整体跳过 vault 预检（如全 copilot 运行无需任何 Azure 路由）。
    """
    from ..core.vault_preflight import DEFAULT_REQUIRED_ROUTES

    routes = list(required) if required is not None else list(DEFAULT_REQUIRED_ROUTES)
    return [r for r in routes if _preflight_route_on_codex(r)]


def _worker_vault_preflight_routes(worker_backend: str) -> list[str]:
    """Return Codex routes to probe for this worker; memory never uses providers."""
    if str(worker_backend or "").strip().lower() == "memory":
        return []
    return required_codex_routes()


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
