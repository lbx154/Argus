from __future__ import annotations

import json
import os
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from argus_skill.maintenance.doctor import DoctorContext, run_full_doctor
from argus_skill.maintenance.models import DoctorFinding, RepairAction
from argus_skill.maintenance.repair import (
    apply_plan,
    create_plan,
    prepare_pr_report,
    read_path_memory,
    submit_pr,
    write_path_memory,
)


def _context(tmp_path: Path) -> DoctorContext:
    checkout = tmp_path / "Argus"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname='argus-skill'\n", encoding="utf-8")
    (checkout / "argus_skill").mkdir()
    return DoctorContext(
        global_root=tmp_path / "state",
        project_root=tmp_path / "state" / "projects" / "s-test",
        checkout=checkout,
        python_executable=Path(os.sys.executable),
        web_host="127.0.0.1",
        web_port=8799,
    )


def test_full_doctor_returns_typed_cross_platform_inventory(tmp_path: Path) -> None:
    report = run_full_doctor(_context(tmp_path), include_backend=False)

    assert report.schema_version == 1
    assert report.target_fingerprint
    assert {finding.scope for finding in report.findings} >= {
        "host", "install", "cli", "web", "desktop", "daemon",
    }
    assert all(finding.code and finding.status for finding in report.findings)


def test_doctor_accepts_wheel_and_frozen_install_modes_without_checkout(
    tmp_path: Path,
) -> None:
    base = _context(tmp_path)
    for mode in ("wheel", "frozen"):
        context = replace(base, checkout=None, install_mode=mode)
        report = run_full_doctor(context, include_backend=False)
        finding = next(item for item in report.findings if item.code == "ARGUS-INSTALL-001")
        assert finding.ok is True
        assert finding.status == f"{mode}_install"


def test_doctor_distinguishes_an_occupied_non_http_port(tmp_path: Path) -> None:
    context = _context(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    try:
        report = run_full_doctor(
            replace(context, web_port=listener.getsockname()[1]),
            include_backend=False,
        )
    finally:
        listener.close()

    finding = next(item for item in report.findings if item.code == "ARGUS-WEB-001")
    assert finding.status == "occupied_unresponsive"
    assert finding.ok is False


def test_doctor_is_read_only(tmp_path: Path) -> None:
    context = _context(tmp_path)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    run_full_doctor(context, include_backend=False)

    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert after == before


def test_doctor_classifies_missing_desktop_runtime_as_repairable(tmp_path: Path) -> None:
    context = _context(tmp_path)
    desktop = context.checkout / "desktop"
    desktop.mkdir()
    (desktop / "package.json").write_text("{}", encoding="utf-8")

    report = run_full_doctor(context, include_backend=False)
    finding = next(item for item in report.findings if item.code == "ARGUS-DESKTOP-001")

    assert finding.status == "electron_binary_missing"
    assert finding.severity == "warning"
    assert finding.repair_action_ids == ("install_electron_binary",)


def test_doctor_surfaces_unresolved_desktop_startup_error(tmp_path: Path) -> None:
    base = _context(tmp_path)
    user_data = tmp_path / "desktop-user-data"
    log = user_data / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "backend ready: Argus Desktop ready\nbackend failed to start: Electron uninstall\n",
        encoding="utf-8",
    )

    report = run_full_doctor(
        replace(base, desktop_user_data=user_data), include_backend=False
    )
    finding = next(item for item in report.findings if item.code == "ARGUS-DESKTOP-LOG-001")

    assert finding.status == "recent_startup_error"
    assert finding.ok is False


def test_doctor_reports_stalled_daemon_separately_from_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    context = _context(tmp_path)
    context.project_root.mkdir(parents=True)
    monkeypatch.setattr(
        "argus_skill.daemon.state.read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=123,
            health_state="stalled",
            stalled=True,
            started_at_iso="2026-08-14T00:00:00+00:00",
            seconds_since_progress=3600.0,
        ),
    )

    report = run_full_doctor(context, include_backend=False)
    finding = next(item for item in report.findings if item.code == "ARGUS-DAEMON-001")

    assert finding.status == "stalled"
    assert finding.ok is False


def test_doctor_does_not_time_box_a_live_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    context = _context(tmp_path)
    context.project_root.mkdir(parents=True)
    (context.project_root / "daemon.drain-request.json").write_text(
        json.dumps({"pid": 123, "requested_at": 1.0}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "argus_skill.daemon.state.read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=123,
            health_state="active",
            stalled=False,
            started_at_iso="2026-08-14T00:00:00+00:00",
            seconds_since_progress=10.0,
        ),
    )

    report = run_full_doctor(context, include_backend=False)
    finding = next(item for item in report.findings if item.code == "ARGUS-DAEMON-001")
    assert finding.status == "draining"
    assert finding.ok is True
    assert finding.repair_action_ids == ()


def test_plan_persists_registered_actions_and_never_shell_text(tmp_path: Path) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-STATE-001",
        scope="daemon",
        severity="error",
        ok=False,
        status="stale_pid",
        detail="stale pid; rm -rf should remain inert text",
        recommendation="rm -rf /must-not-run",
        repair_action_ids=("remove_verified_stale_daemon_pid",),
    )

    plan = create_plan(context, [finding])
    payload = json.loads(plan.path.read_text(encoding="utf-8"))

    assert payload["plan_id"] == plan.plan_id
    assert payload["actions"][0]["id"] == "remove_verified_stale_daemon_pid"
    assert "command" not in payload["actions"][0]
    assert not (tmp_path / "must-not-run").exists()


def test_safe_apply_removes_only_verified_stale_pid_and_records_history(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.project_root.mkdir(parents=True)
    pid_path = context.project_root / "daemon.pid"
    pid_path.write_text("2000000000\n", encoding="ascii")
    finding = DoctorFinding(
        code="ARGUS-STATE-001",
        scope="daemon",
        severity="error",
        ok=False,
        status="stale_pid",
        detail="dead pid",
        repair_action_ids=("remove_verified_stale_daemon_pid",),
    )
    plan = create_plan(context, [finding])

    result = apply_plan(context, plan.plan_id, safe_only=True)

    assert result.status == "completed"
    assert not pid_path.exists()
    assert result.actions[0]["status"] == "applied"
    assert (context.global_root / "repairs" / "history.jsonl").exists()
    memory = read_path_memory(context.global_root)
    assert memory["checkout"] == str(context.checkout.resolve())
    assert memory["python_executable"] == str(context.python_executable.resolve())


def test_apply_refuses_when_target_fingerprint_changed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-PATH-001",
        scope="install",
        severity="warning",
        ok=False,
        status="path_memory_missing",
        detail="missing",
        repair_action_ids=("refresh_path_memory",),
    )
    plan = create_plan(context, [finding])
    changed = DoctorContext(**{**context.__dict__, "web_port": 8800})

    with pytest.raises(RuntimeError, match="target fingerprint changed"):
        apply_plan(changed, plan.plan_id, safe_only=True)


def test_interrupted_plan_reconciles_without_repeating_applied_actions(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-PATH-001",
        scope="install",
        severity="warning",
        ok=False,
        status="path_memory_missing",
        detail="missing",
        repair_action_ids=("refresh_path_memory",),
    )
    plan = create_plan(context, [finding])
    write_path_memory(context)
    payload = json.loads(plan.path.read_text(encoding="utf-8"))
    payload["status"] = "running"
    payload["outcomes"] = [{"id": "refresh_path_memory", "status": "applied"}]
    plan.path.write_text(json.dumps(payload), encoding="utf-8")

    result = apply_plan(context, plan.plan_id, safe_only=True)

    assert result.status == "completed"
    assert result.actions == ({"id": "refresh_path_memory", "status": "already_applied"},)
    history = (context.global_root / "repairs" / "history.jsonl").read_text(encoding="utf-8")
    assert "repair.plan.reconciled" in history


def test_apply_fails_when_registered_verification_does_not_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-PATH-001",
        scope="install",
        severity="warning",
        ok=False,
        status="path_memory_missing",
        detail="missing",
        repair_action_ids=("refresh_path_memory",),
    )
    plan = create_plan(context, [finding])
    monkeypatch.setattr(
        "argus_skill.maintenance.repair.write_path_memory",
        lambda _context: context.global_root / "repairs" / "path-memory.json",
    )

    result = apply_plan(context, plan.plan_id, safe_only=True)

    assert result.status == "failed"
    payload = json.loads(plan.path.read_text(encoding="utf-8"))
    assert payload["verification_failures"] == ["ARGUS-PATH-001"]


def test_completed_plan_is_idempotent(tmp_path: Path) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-PATH-001",
        scope="install",
        severity="warning",
        ok=False,
        status="path_memory_missing",
        detail="missing",
        repair_action_ids=("refresh_path_memory",),
    )
    plan = create_plan(context, [finding])

    first = apply_plan(context, plan.plan_id, safe_only=True)
    second = apply_plan(context, plan.plan_id, safe_only=True)

    assert first.status == "completed"
    assert second.status == "already_applied"


def test_prepare_pr_report_is_sanitized_and_does_not_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    secret = "ghp_NOT_A_REAL_TOKEN"
    finding = DoctorFinding(
        code="ARGUS-INSTALL-001",
        scope="install",
        severity="error",
        ok=False,
        status="broken_install",
        detail=f"token={secret} under {Path.home()}",
        repair_action_ids=("install_editable",),
    )
    plan = create_plan(context, [finding])
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("prepare-pr must not publish")

    monkeypatch.setattr("subprocess.run", forbidden)
    report = prepare_pr_report(context, plan.plan_id)
    text = report.read_text(encoding="utf-8")

    assert secret not in plan.path.read_text(encoding="utf-8")
    assert secret not in text
    assert str(Path.home()) not in text
    assert "ARGUS-INSTALL-001" in text
    assert called is False


def test_submit_pr_requires_explicit_authorization(tmp_path: Path) -> None:
    context = _context(tmp_path)
    finding = DoctorFinding(
        code="ARGUS-ASSET-001",
        scope="install",
        severity="error",
        ok=False,
        status="assets_missing",
        detail="missing",
        repair_action_ids=("rebuild_release_assets",),
    )
    plan = create_plan(context, [finding])

    with pytest.raises(PermissionError, match="--yes"):
        submit_pr(context, plan.plan_id, confirmed=False)


def test_repair_action_model_has_no_arbitrary_command_field() -> None:
    action = RepairAction(
        id="refresh_path_memory",
        provider="core",
        risk="safe",
        target="paths",
    )
    assert not hasattr(action, "command")
