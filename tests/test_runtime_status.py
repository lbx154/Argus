from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import runtime_status, state
from argus.daemon.runtime_status import (
    observe_runtime_status,
    register_web_status,
    render_runtime_status,
)
from argus.life.memory import Backlog, BacklogItem


def project(owner, sid, name=""):
    root = owner / "projects" / sid
    root.mkdir(parents=True)
    write_session_meta(owner, SessionMeta(id=sid, display_name=name))
    return root


def test_empty_project_does_not_hide_a_running_peer_or_web_service(tmp_path, monkeypatch):
    current = project(tmp_path, "s-empty")
    other = project(tmp_path, "s-training", "训练实验")
    item = BacklogItem.new(title="训练模型", objective="Train the model")
    item.status = "running"
    Backlog(other / "backlog.jsonl").add(item)
    monkeypatch.setattr(state, "read_daemon_status", lambda root: SimpleNamespace(alive=root == other))
    close = register_web_status(tmp_path, lambda sid: int(sid == current.name))
    try:
        facts = observe_runtime_status(current, "argus_status")
    finally:
        close()
    assert facts["service"]["webapi"] == "running"
    assert facts["current_project"]["daemon_alive"] is False
    assert facts["current_project"]["foreground_requests"] == 1
    assert next(row for row in facts["projects"] if row["id"] == other.name)["state"] == "active"
    answer = render_runtime_status(facts, chinese=True)
    assert "网页服务正在运行" in answer and "后台执行进程未启动" in answer
    assert "训练实验" in answer and "训练模型" in answer
    assert "整个服务器没有" not in answer


def test_foreground_work_is_visible_without_a_daemon(tmp_path):
    current = project(tmp_path, "s-current")
    other = project(tmp_path, "s-upload", "文件分析")
    close = register_web_status(tmp_path, lambda sid: int(sid == other.name))
    try:
        facts = observe_runtime_status(current, "argus_status")
    finally:
        close()
    peer = next(row for row in facts["projects"] if row["id"] == other.name)
    assert peer["daemon_alive"] is False
    assert peer["foreground_requests"] == 1 and peer["state"] == "active"


def test_absent_observation_is_not_idle_and_stale_work_is_not_running(tmp_path):
    current = project(tmp_path, "s-current")
    item = BacklogItem.new(title="Unfinished experiment", objective="Inspect the stopped run")
    item.status = "running"
    Backlog(current / "backlog.jsonl").add(item)
    facts = observe_runtime_status(current, "project_status")
    assert facts["current_project"]["state"] == "interrupted"
    assert facts["service"]["webapi"] == "unobserved"
    answer = render_runtime_status(facts, chinese=False)
    assert "worker offline" in answer and "not observed" in answer
    assert facts["projects"] == [] and "host" not in facts


def test_project_scan_is_bounded_and_does_not_follow_other_tenants(tmp_path, monkeypatch):
    current = project(tmp_path / "owner", "s-current")
    project(tmp_path / "owner", "s-peer")
    outside = project(tmp_path / "another-owner", "s-private", "PRIVATE_PROJECT")
    (current.parent / "s-link").symlink_to(outside, target_is_directory=True)
    facts = observe_runtime_status(current, "argus_status")
    assert {row["id"] for row in facts["projects"]} == {"s-current", "s-peer"}
    assert "PRIVATE_PROJECT" not in str(facts)
    monkeypatch.setattr(runtime_status, "_PROJECT_LIMIT", 1)
    facts = observe_runtime_status(current, "argus_status")
    assert not facts["project_scan_complete"]
    assert "scan is incomplete" in render_runtime_status(facts, chinese=False)


def test_host_probe_never_reads_commands_or_environment(tmp_path, monkeypatch):
    import psutil

    current = project(tmp_path, "s-current")
    calls = []

    def processes(attrs, ad_value):
        calls.append(attrs)
        return iter([
            SimpleNamespace(info={"username": "service", "name": "python"}),
            SimpleNamespace(info={"username": "other", "name": "PRIVATE_PROCESS"}),
            SimpleNamespace(info={"username": None, "name": None}),
        ])

    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(username=lambda: "service"))
    monkeypatch.setattr(psutil, "process_iter", processes)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 8.5)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(percent=12.5))
    facts = observe_runtime_status(current, "host_status")
    assert calls == [["name", "username"]]
    assert facts["host"]["process_names"] == {"python": 1}
    assert facts["host"]["unreadable_processes"] == 1
    assert "PRIVATE_PROCESS" not in str(facts)
    assert "not all Argus agents" in render_runtime_status(facts, chinese=False)


def test_web_lifecycle_and_other_app_requests_are_isolated(tmp_path):
    from argus.webapi.server import create_app

    owner = tmp_path / "one"
    other_owner = tmp_path / "two"
    current = project(owner, "s-same")
    other = project(other_owner, "s-same")
    app = create_app(global_root=owner)
    other_app = create_app(global_root=other_owner)
    with TestClient(app), TestClient(other_app):
        lease = other_app.state.message_requests.begin("s-same")
        try:
            assert observe_runtime_status(current, "project_status")["current_project"]["foreground_requests"] == 0
            assert observe_runtime_status(other, "project_status")["current_project"]["foreground_requests"] == 1
        finally:
            lease.finish()
    assert observe_runtime_status(current, "project_status")["service"]["webapi"] == "unobserved"
