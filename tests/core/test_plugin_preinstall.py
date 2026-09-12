"""Plugins a deployment declares are prepared once, quietly, and shown as provided."""

import json
import threading
import time

import pytest

from argus_skill.core import plugin_manager as pm


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path / "host"))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "pi")
    for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER"):
        monkeypatch.setenv("ARGUS_SKILL_" + role + "_BACKEND", "pi")
    monkeypatch.delenv(pm.PREINSTALL_ENV, raising=False)
    # Registry rows in these tests point at no real package; loading one is not the subject.
    monkeypatch.setattr(pm, "load_plugin", lambda *args, **kwargs: None)
    return tmp_path / "host"


def current_row(**changes):
    spec = pm.catalog()["crystalpilot"]
    row = {
        "version": spec["version"],
        "release": "crystalpilot/releases/current",
        "python": "/science/bin/python",
        "enabled": True,
        "installed": 1.0,
        "sha256": spec["artifact"]["sha256"],
        "python_constraints": spec.get("python_constraints", []),
    }
    row.update(changes)
    return row


def write_registry(host, row):
    pm.write_json(pm.install_root(host) / "registry.json", {"crystalpilot": row})


@pytest.fixture
def slow_install(monkeypatch):
    """Stand in for the verified install: block until released, then activate."""
    release = threading.Event()
    calls = []

    def install(name, spec, root, action="install"):
        calls.append(action)
        release.wait(10)
        write_registry(root, current_row())
        path = pm.install_root(root) / name / "operation.json"
        pm.write_json(path, {**pm.read_json(path), "status": "completed", "completed": time.time()})

    monkeypatch.setattr(pm, "_install", install)
    return release, calls


def test_declared_ids_are_read_once_each_in_order(monkeypatch):
    monkeypatch.setenv(pm.PREINSTALL_ENV, " crystalpilot, ,other ,crystalpilot")
    assert pm.preinstalled_ids() == ["crystalpilot", "other"]
    assert pm.managed_by_host("crystalpilot") and not pm.managed_by_host("absent")
    monkeypatch.delenv(pm.PREINSTALL_ENV)
    assert pm.preinstalled_ids() == [] and not pm.managed_by_host("crystalpilot")


def test_current_plugin_is_left_untouched(host, monkeypatch):
    write_registry(host, current_row())
    monkeypatch.setattr(pm, "mutate", lambda *a, **k: pytest.fail("nothing should be started"))
    for _ in range(2):
        assert pm.preinstall(host, ids=["crystalpilot"]) == {"crystalpilot": {"status": "ready"}}
    assert not (pm.install_root(host) / "crystalpilot" / "operation.json").exists()


def test_changed_scientific_constraints_require_an_update(host, slow_install):
    release, calls = slow_install
    release.set()
    write_registry(host, current_row(python_constraints=[]))
    assert pm.preinstall_need("crystalpilot", host) == "update"
    assert pm.plugin_rows(host)[0]["update_available"] is True
    assert pm.preinstall(host, ids=["crystalpilot"], wait=True, timeout=10) == {
        "crystalpilot": {"status": "completed", "action": "update"},
    }
    assert calls == ["update"]
    assert pm.preinstall_need("crystalpilot", host) is None


def test_missing_plugin_is_installed_once_even_when_asked_again(host, slow_install):
    release, calls = slow_install
    assert pm.preinstall(host, ids=["crystalpilot"]) == {
        "crystalpilot": {"status": "running", "action": "install"}
    }
    # A second startup or a repeated call while the job runs must not start another.
    assert pm.preinstall(host, ids=["crystalpilot"]) == {
        "crystalpilot": {"status": "running", "action": "install"}
    }
    assert calls == ["install"]
    release.set()
    pm.wait_for_operation("crystalpilot", host, timeout=10)
    assert pm.preinstall(host, ids=["crystalpilot"]) == {"crystalpilot": {"status": "ready"}}
    assert calls == ["install"]


def test_outdated_plugin_is_updated_and_disabled_plugin_is_enabled(host, slow_install):
    release, calls = slow_install
    release.set()
    write_registry(host, current_row(version="0.3.9", sha256="0" * 64))
    assert pm.preinstall_need("crystalpilot", host) == "update"
    assert pm.preinstall(host, ids=["crystalpilot"], wait=True, timeout=10) == {
        "crystalpilot": {"status": "completed", "action": "update"}
    }
    assert calls == ["update"]
    write_registry(host, current_row(enabled=False))
    assert pm.preinstall_need("crystalpilot", host) == "enable"
    assert pm.preinstall(host, ids=["crystalpilot"]) == {
        "crystalpilot": {"status": "completed", "action": "enable"}
    }
    assert pm.registry(host)["crystalpilot"]["enabled"] is True
    assert pm.preinstall_need("crystalpilot", host) is None


def test_failed_install_is_reported_not_raised(host, monkeypatch):
    def failing(name, spec, root, action="install"):
        path = pm.install_root(root) / name / "operation.json"
        pm.write_json(path, {**pm.read_json(path), "status": "failed", "error": "插件包校验失败，未安装。"})

    monkeypatch.setattr(pm, "_install", failing)
    result = pm.preinstall(host, ids=["crystalpilot", "unknown"], wait=True, timeout=10)
    assert result["crystalpilot"] == {
        "status": "failed", "action": "install", "error": "插件包校验失败，未安装。"
    }
    assert result["unknown"] == {"status": "failed", "error": "Unknown plugin"}


def test_declared_plugins_are_marked_managed_and_keep_their_place(host, monkeypatch):
    write_registry(host, current_row())
    assert pm.plugin_rows(host)[0]["managed_by_host"] is False
    monkeypatch.setenv(pm.PREINSTALL_ENV, "crystalpilot")
    row = pm.plugin_rows(host)[0]
    assert row["managed_by_host"] is True and row["installed"] and row["enabled"]
    for action in ("disable", "uninstall"):
        with pytest.raises(pm.PluginError, match="服务方"):
            pm.mutate("crystalpilot", action, host)
    assert pm.registry(host)["crystalpilot"]["enabled"] is True
    # Enabling stays possible: it is what startup does for a disabled copy.
    write_registry(host, current_row(enabled=False))
    assert pm.mutate("crystalpilot", "enable", host) == {"status": "completed", "data_retained": True}


def test_web_server_prepares_declared_plugins_once_at_startup(host, monkeypatch):
    from fastapi.testclient import TestClient

    from argus_skill.webapi.server import create_app

    calls = []
    monkeypatch.setattr(pm, "preinstall", lambda root, **kw: calls.append((root, kw["logger"].name)))
    with TestClient(create_app(global_root=host, auth_token="test")):
        pass
    assert calls == []
    monkeypatch.setenv(pm.PREINSTALL_ENV, "crystalpilot")
    with TestClient(create_app(global_root=host, auth_token="test")):
        for thread in threading.enumerate():
            if thread.name == "plugin-preinstall":
                thread.join(10)
    assert calls == [(host, "uvicorn.error")]


def test_image_build_command_installs_and_reports(host, slow_install, capsys):
    from argus_skill.release_tools import preinstall_plugins

    release, calls = slow_install
    release.set()
    root = host / "shared"
    assert preinstall_plugins.main(["crystalpilot", "--root", str(root), "--timeout", "10"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["root"] == str(root.resolve())
    assert report["plugins"] == {"crystalpilot": {"status": "completed", "action": "install"}}
    assert calls == ["install"]
    assert pm.registry(root)["crystalpilot"]["enabled"] is True
    assert preinstall_plugins.main(["crystalpilot", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["plugins"] == {"crystalpilot": {"status": "ready"}}
    with pytest.raises(SystemExit):
        preinstall_plugins.main(["--root", str(root)])
