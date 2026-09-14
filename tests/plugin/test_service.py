from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.plugin.service import ArgusOperations, ArgusPluginService


def _fake_operations(
    *,
    manager_result: dict[str, Any] | None = None,
    projects: list[dict[str, Any]] | None = None,
    status_result: dict[str, Any] | None = None,
    doctor_result: dict[str, Any] | None = None,
    artifacts_result: list[dict[str, Any]] | None = None,
    start_result: dict[str, Any] | None = None,
    stop_result: dict[str, Any] | None = None,
) -> tuple[ArgusOperations, list[tuple[Any, ...]]]:
    calls: list[tuple[Any, ...]] = []

    def create_project(objective: str = "", **kwargs: Any) -> dict[str, Any]:
        calls.append(("create_project", objective, kwargs))
        return {"sid": "s-new", "workdir": kwargs["workdir"], "daemon": {"alive": False}}

    def list_projects(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(("list_projects", kwargs))
        return list(projects or [])

    def manager_message(project_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(("manager_message", project_id, text, kwargs))
        return dict(manager_result or {"kind": "chat", "reply": "ok"})

    def start_daemon(project_id: str, **kwargs: Any) -> dict[str, Any] | None:
        calls.append(("start_daemon", project_id, kwargs))
        return dict(start_result or {"rc": 0, "daemon": {"alive": True}})

    def record_dispatch_ack(
        project_id: str,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        calls.append(("record_dispatch_ack", project_id, result.get("kind"), kwargs))
        result["reply"] = "executor started"
        return result["reply"]

    def status(project_id: str, **kwargs: Any) -> dict[str, Any] | None:
        calls.append(("status", project_id, kwargs))
        return None if status_result is None else dict(status_result)

    def doctor(project_id: str, **kwargs: Any) -> dict[str, Any] | None:
        calls.append(("doctor", project_id, kwargs))
        return None if doctor_result is None else dict(doctor_result)

    def stop_daemon(project_id: str, **kwargs: Any) -> dict[str, Any] | None:
        calls.append(("stop_daemon", project_id, kwargs))
        return dict(stop_result or {"rc": 0})

    def artifacts(project_id: str, **kwargs: Any) -> list[dict[str, Any]] | None:
        calls.append(("artifacts", project_id, kwargs))
        return None if artifacts_result is None else [dict(row) for row in artifacts_result]

    return (
        ArgusOperations(
            create_project=create_project,
            list_projects=list_projects,
            manager_message=manager_message,
            start_daemon=start_daemon,
            record_dispatch_ack=record_dispatch_ack,
            status=status,
            doctor=doctor,
            stop_daemon=stop_daemon,
            artifacts=artifacts,
        ),
        calls,
    )


def test_create_project_binds_existing_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    operations, calls = _fake_operations()
    service = ArgusPluginService(global_root=tmp_path / "state", operations=operations)

    result = service.create_project(str(workdir), name="Medical review")

    assert result["ok"] is True
    assert result["project"]["sid"] == "s-new"
    operation, objective, kwargs = calls[0]
    assert operation == "create_project"
    assert objective == ""
    assert kwargs["name"] == "Medical review"
    assert kwargs["workdir"] == str(workdir.resolve())
    assert kwargs["launch_cwd"] == str(workdir.resolve())
    assert kwargs["global_root"] == tmp_path / "state"


def test_create_project_rejects_missing_workdir(tmp_path: Path) -> None:
    operations, calls = _fake_operations()
    service = ArgusPluginService(operations=operations)

    result = service.create_project(str(tmp_path / "missing"))

    assert result == {
        "ok": False,
        "error_code": "invalid_workdir",
        "error": f"workdir is not an existing directory: {tmp_path / 'missing'}",
    }
    assert calls == []


def test_list_projects_filters_by_exact_resolved_workdir(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    selected.mkdir()
    other.mkdir()
    operations, _calls = _fake_operations(
        projects=[
            {"id": "s1", "workdir": str(selected), "last_active": 2},
            {"id": "s2", "workdir": str(other), "last_active": 3},
            {"id": "s3", "workdir": str(selected), "last_active": 1},
        ]
    )
    service = ArgusPluginService(operations=operations)

    result = service.list_projects(str(selected))

    assert result["ok"] is True
    assert [row["id"] for row in result["projects"]] == ["s1", "s3"]


def test_message_routes_through_manager_and_starts_real_daemon(tmp_path: Path) -> None:
    operations, calls = _fake_operations(
        manager_result={"kind": "task", "item": {"id": "i1"}}
    )
    service = ArgusPluginService(global_root=tmp_path, operations=operations)

    result = service.message("s1", "research EGFR")

    assert result["ok"] is True
    assert result["daemon"]["rc"] == 0
    assert [call[0] for call in calls] == [
        "manager_message",
        "start_daemon",
        "record_dispatch_ack",
    ]
    assert calls[0] == (
        "manager_message",
        "s1",
        "research EGFR",
        {"global_root": tmp_path, "source_channel": "plugin"},
    )
    assert calls[1] == (
        "start_daemon",
        "s1",
        {"global_root": tmp_path, "reclaim_idle": True},
    )
    assert calls[2] == (
        "record_dispatch_ack",
        "s1",
        "task",
        {"global_root": tmp_path},
    )


def test_chat_message_does_not_start_daemon() -> None:
    operations, calls = _fake_operations(
        manager_result={"kind": "chat", "reply": "hello"}
    )
    service = ArgusPluginService(operations=operations)

    result = service.message("s1", "hello")

    assert result == {"ok": True, "kind": "chat", "reply": "hello"}
    assert [call[0] for call in calls] == ["manager_message"]


def test_manager_error_remains_a_failure() -> None:
    operations, calls = _fake_operations(
        manager_result={"kind": "error", "reply": "backend unavailable"}
    )
    service = ArgusPluginService(operations=operations)

    result = service.message("s1", "do work")

    assert result["ok"] is False
    assert result["error_code"] == "manager_error"
    assert result["error"] == "backend unavailable"
    assert [call[0] for call in calls] == ["manager_message"]


def test_daemon_admission_failure_is_not_reported_as_running() -> None:
    operations, calls = _fake_operations(
        manager_result={"kind": "task", "item": {"id": "i1"}},
        start_result={"rc": 2, "error": "active daemon limit reached"},
    )
    service = ArgusPluginService(operations=operations)

    result = service.message("s1", "do work")

    assert result["ok"] is False
    assert result["error_code"] == "daemon_start_failed"
    assert result["error"] == "active daemon limit reached"
    assert result["kind"] == "task"
    assert [call[0] for call in calls] == [
        "manager_message",
        "start_daemon",
        "record_dispatch_ack",
    ]


def test_status_and_doctor_report_unknown_project() -> None:
    operations, _calls = _fake_operations()
    service = ArgusPluginService(operations=operations)

    assert service.status("missing")["error_code"] == "project_not_found"
    assert service.doctor("missing")["error_code"] == "project_not_found"


def test_stop_defaults_to_graceful_drain() -> None:
    operations, calls = _fake_operations()
    service = ArgusPluginService(operations=operations)

    result = service.stop("s1")

    assert result == {"ok": True, "result": {"rc": 0}}
    assert calls[-1] == (
        "stop_daemon",
        "s1",
        {"drain": True, "force": False, "global_root": None},
    )


def test_stop_is_idempotent_when_daemon_is_already_stopped() -> None:
    operations, _calls = _fake_operations(stop_result={"rc": 1})
    service = ArgusPluginService(operations=operations)

    result = service.stop("s1")

    assert result == {
        "ok": True,
        "result": {"rc": 0, "source_rc": 1, "already_stopped": True},
    }


def test_artifacts_returns_only_existing_allowlisted_rows() -> None:
    operations, _calls = _fake_operations(
        artifacts_result=[
            {"path": "report.md", "exists": True},
            {"path": "missing.md", "exists": False},
        ]
    )
    service = ArgusPluginService(operations=operations)

    result = service.artifacts("s1")

    assert result == {
        "ok": True,
        "artifacts": [{"path": "report.md", "exists": True}],
    }
