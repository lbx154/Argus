"""Transport-neutral host-plugin facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ArgusOperations:
    create_project: Callable[..., dict[str, Any]]
    list_projects: Callable[..., list[dict[str, Any]]]
    manager_message: Callable[..., dict[str, Any]]
    start_daemon: Callable[..., dict[str, Any] | None]
    record_dispatch_ack: Callable[..., str]
    status: Callable[..., dict[str, Any] | None]
    doctor: Callable[..., dict[str, Any] | None]
    stop_daemon: Callable[..., dict[str, Any] | None]
    artifacts: Callable[..., list[dict[str, Any]] | None]


def _default_operations() -> ArgusOperations:
    from ..webapi.artifacts import list_project_artifacts
    from ..webapi.daemon_lifecycle import (
        create_daemon,
        start_project_daemon,
        stop_project_daemon,
    )
    from ..webapi.manager_bridge import manager_message
    from ..webapi.manager_pending_question import record_task_dispatch_ack
    from ..webapi.mission_items import get_doctor, get_status
    from ..webapi.project_state import list_projects

    return ArgusOperations(
        create_project=create_daemon,
        list_projects=list_projects,
        manager_message=manager_message,
        start_daemon=start_project_daemon,
        record_dispatch_ack=record_task_dispatch_ack,
        status=get_status,
        doctor=get_doctor,
        stop_daemon=stop_project_daemon,
        artifacts=list_project_artifacts,
    )


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "error": message,
        **details,
    }


class ArgusPluginService:
    def __init__(
        self,
        *,
        global_root: Path | str | None = None,
        operations: ArgusOperations | None = None,
    ) -> None:
        self.global_root = Path(global_root) if global_root is not None else None
        self.operations = operations or _default_operations()

    def create_project(self, workdir: str, *, name: str = "") -> dict[str, Any]:
        candidate = Path(str(workdir or "")).expanduser()
        if not candidate.is_dir():
            return _error(
                "invalid_workdir",
                f"workdir is not an existing directory: {candidate}",
            )
        resolved = candidate.resolve()
        try:
            project = self.operations.create_project(
                "",
                name=str(name or "").strip(),
                launch_cwd=str(resolved),
                workdir=str(resolved),
                global_root=self.global_root,
            )
        except Exception as exc:  # noqa: BLE001 - plugin boundary is fail-visible
            return _error(
                "project_create_failed",
                f"Argus project creation failed: {type(exc).__name__}: {exc}",
            )
        return {"ok": True, "project": project}

    def list_projects(self, workdir: str = "") -> dict[str, Any]:
        target: Path | None = None
        if str(workdir or "").strip():
            candidate = Path(workdir).expanduser()
            if not candidate.is_dir():
                return _error(
                    "invalid_workdir",
                    f"workdir is not an existing directory: {candidate}",
                )
            target = candidate.resolve()
        try:
            projects = self.operations.list_projects(
                global_root=self.global_root,
                limit=2000,
                include_empty=True,
            )
        except Exception as exc:  # noqa: BLE001 - plugin boundary is fail-visible
            return _error(
                "project_list_failed",
                f"Argus project listing failed: {type(exc).__name__}: {exc}",
            )
        if target is not None:
            filtered: list[dict[str, Any]] = []
            for row in projects:
                raw = str(row.get("workdir") or "").strip()
                if not raw:
                    continue
                try:
                    if Path(raw).expanduser().resolve() == target:
                        filtered.append(row)
                except (OSError, RuntimeError):
                    continue
            projects = filtered
        projects = sorted(
            projects,
            key=lambda row: float(row.get("last_active") or 0.0),
            reverse=True,
        )
        return {"ok": True, "projects": projects}

    def message(self, project_id: str, text: str) -> dict[str, Any]:
        sid = str(project_id or "").strip()
        body = str(text or "").strip()
        if not sid:
            return _error("invalid_project_id", "project_id is required")
        if not body:
            return _error("invalid_message", "message text is required")
        try:
            result = self.operations.manager_message(
                sid,
                body,
                global_root=self.global_root,
                source_channel="plugin",
            )
        except Exception as exc:  # noqa: BLE001 - preserve backend failure
            return _error(
                "manager_failed",
                f"Argus Manager failed: {type(exc).__name__}: {exc}",
            )
        kind = str(result.get("kind") or "").strip().lower()
        if kind == "error":
            message = str(result.get("error") or result.get("reply") or "Manager error")
            return _error("manager_error", message, **result)
        starts_executor = kind == "task" or (
            kind == "pending_question" and bool(result.get("resolved"))
        )
        daemon_rc = 0
        if starts_executor and not bool(result.get("daemon_alive")):
            try:
                daemon = self.operations.start_daemon(
                    sid,
                    global_root=self.global_root,
                    reclaim_idle=True,
                )
            except Exception as exc:  # noqa: BLE001 - preserve dispatch result
                return _error(
                    "daemon_start_failed",
                    f"Argus daemon failed to start: {type(exc).__name__}: {exc}",
                    **result,
                )
            if daemon is None:
                return _error(
                    "project_not_found",
                    f"unknown Argus project: {sid}",
                    **result,
                )
            result["daemon"] = daemon
            try:
                daemon_rc = int(daemon.get("rc", 0) or 0)
            except (TypeError, ValueError):
                daemon_rc = 2
        if kind == "task":
            try:
                self.operations.record_dispatch_ack(
                    sid,
                    result,
                    global_root=self.global_root,
                )
            except Exception as exc:  # noqa: BLE001 - dispatch durability is required
                return _error(
                    "dispatch_ack_failed",
                    f"Argus dispatch acknowledgement failed: {type(exc).__name__}: {exc}",
                    **result,
                )
        if daemon_rc != 0:
            daemon = result.get("daemon")
            daemon_error = daemon.get("error") if isinstance(daemon, dict) else ""
            return _error(
                "daemon_start_failed",
                str(daemon_error or f"Argus daemon exited with rc={daemon_rc}"),
                **result,
            )
        return {"ok": True, **result}

    def status(self, project_id: str) -> dict[str, Any]:
        sid = str(project_id or "").strip()
        try:
            value = self.operations.status(sid, global_root=self.global_root)
        except Exception as exc:  # noqa: BLE001
            return _error(
                "status_failed",
                f"Argus status failed: {type(exc).__name__}: {exc}",
            )
        if value is None:
            return _error("project_not_found", f"unknown Argus project: {sid}")
        return {"ok": True, "status": value}

    def doctor(self, project_id: str) -> dict[str, Any]:
        sid = str(project_id or "").strip()
        try:
            value = self.operations.doctor(sid, global_root=self.global_root)
        except Exception as exc:  # noqa: BLE001
            return _error(
                "doctor_failed",
                f"Argus diagnostics failed: {type(exc).__name__}: {exc}",
            )
        if value is None:
            return _error("project_not_found", f"unknown Argus project: {sid}")
        return {"ok": True, "doctor": value}

    def stop(self, project_id: str, *, force: bool = False) -> dict[str, Any]:
        sid = str(project_id or "").strip()
        try:
            value = self.operations.stop_daemon(
                sid,
                drain=not force,
                force=bool(force),
                global_root=self.global_root,
            )
        except Exception as exc:  # noqa: BLE001
            return _error(
                "stop_failed",
                f"Argus stop failed: {type(exc).__name__}: {exc}",
            )
        if value is None:
            return _error("project_not_found", f"unknown Argus project: {sid}")
        try:
            rc = int(value.get("rc", 0) or 0)
        except (TypeError, ValueError):
            rc = 2
        if rc == 1:
            return {
                "ok": True,
                "result": {
                    **value,
                    "rc": 0,
                    "source_rc": 1,
                    "already_stopped": True,
                },
            }
        if rc != 0:
            return _error(
                "stop_failed",
                str(value.get("error") or f"Argus stop exited with rc={rc}"),
                result=value,
            )
        return {"ok": True, "result": value}

    def artifacts(self, project_id: str) -> dict[str, Any]:
        sid = str(project_id or "").strip()
        try:
            value = self.operations.artifacts(sid, global_root=self.global_root)
        except Exception as exc:  # noqa: BLE001
            return _error(
                "artifacts_failed",
                f"Argus artifact listing failed: {type(exc).__name__}: {exc}",
            )
        if value is None:
            return _error("project_not_found", f"unknown Argus project: {sid}")
        return {
            "ok": True,
            "artifacts": [row for row in value if bool(row.get("exists"))],
        }
