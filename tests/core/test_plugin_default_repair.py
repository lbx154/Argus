"""All normal Windows plugin setup paths must include the PLATON runtime."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from argus.core import plugin_manager as pm

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PLATON setup")


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(pm, "_loaded", {})
    return tmp_path


def healthy(root, *, platon="ready"):
    pm.write_json(pm.install_root(root) / "crystalpilot/resources/health.json", {
        "components": [
            {"id": name, "status": platon if name == "platon" else "ready", "automatic": True}
            for name in ("python", "dials", "systre", "platon")
        ] + [{"id": "shelxt", "status": "missing", "license_required": True}],
    })


def setup_doubles(root, monkeypatch, *, platon="ready"):
    calls = []
    executable = root / "official-platon" / "platon-headless.exe"
    def prepare(_root):
        calls.append("prepare")
        return executable
    def invoke(name, spec, _root, python, action, payload=None):
        calls.append(action)
        if action == "configure":
            assert payload == {"paths": {"platon": str(executable)}}
        healthy(root, platon=platon)
    monkeypatch.setattr(pm, "_prepare_platon_for_setup", prepare)
    monkeypatch.setattr(pm, "_invoke_setup", invoke)
    return calls


def test_default_repair_prepares_and_configures_before_published_recipe(host, monkeypatch):
    calls = setup_doubles(host, monkeypatch)
    pm._run_setup("crystalpilot", {}, host, "fixture-python", "repair", {"accept_software_license": True})
    assert calls == ["prepare", "configure", "repair"]
    assert pm._platon_license_accepted(host)
    calls.clear()
    pm._run_setup("crystalpilot", {}, host, "fixture-python", "repair")
    assert calls == ["prepare", "configure", "repair"]


def test_repair_without_consent_never_runs_the_incomplete_old_recipe(host, monkeypatch):
    monkeypatch.setattr(pm, "_invoke_setup", lambda *a, **k: pytest.fail("No partial EXE-only repair"))
    with pytest.raises(pm.PluginError, match="许可"):
        pm._run_setup("crystalpilot", {}, host, "fixture-python", "repair")


def test_process_exit_zero_is_not_reported_as_success_when_component_missing(host, monkeypatch):
    calls = setup_doubles(host, monkeypatch, platon="missing")
    with pytest.raises(pm.PluginError, match="platon"):
        pm._run_setup("crystalpilot", {}, host, "fixture-python", "repair", {"accept_software_license": True})
    assert calls == ["prepare", "configure", "repair"]


def test_runtime_preparation_failure_does_not_fall_back_to_exe_only_repair(host, monkeypatch):
    pm._remember_platon_consent(host)
    monkeypatch.setattr(pm, "_prepare_platon_for_setup", lambda *_: (_ for _ in ()).throw(RuntimeError("download failed")))
    monkeypatch.setattr(pm, "_invoke_setup", lambda *a, **k: pytest.fail("Do not retry the incomplete recipe"))
    with pytest.raises(RuntimeError, match="download failed"):
        pm._run_setup("crystalpilot", {}, host, "fixture-python", "repair")


def test_automatic_install_uses_the_same_complete_repair_path(host, monkeypatch):
    calls = setup_doubles(host, monkeypatch)
    pm._remember_platon_consent(host)
    spec = pm.catalog()["crystalpilot"]
    monkeypatch.setattr(pm, "_fetch", lambda *a: None)
    monkeypatch.setattr(pm, "_extract", lambda *a: None)
    monkeypatch.setattr(pm, "_python", lambda *_: "fixture-python")
    monkeypatch.setattr(pm.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    plugin = SimpleNamespace(is_busy=lambda: False, shutdown_workers=lambda: None)
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **k: plugin)
    pm._install("crystalpilot", spec, host)
    assert calls == ["prepare", "configure", "repair"]
    assert pm.state_entry("crystalpilot", host)["enabled"] is True
    assert pm.read_json(pm.install_root(host) / "crystalpilot/operation.json")["status"] == "completed"


def test_default_repair_job_marks_incomplete_health_as_failure(host, monkeypatch):
    setup_doubles(host, monkeypatch, platon="broken")
    pm._remember_platon_consent(host)
    pm.write_json(pm.install_root(host) / "crystalpilot/operation.json", {"status": "running", "action": "repair"})
    monkeypatch.setattr(pm, "state_entry", lambda *a: {"python": "fixture-python"})
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **k: None)
    from argus.core import plugin_runtime
    monkeypatch.setattr(plugin_runtime, "run", lambda *a, **k: "")
    pm._setup_job("crystalpilot", pm.catalog()["crystalpilot"], host, "repair", {})
    result = pm.read_json(pm.install_root(host) / "crystalpilot/operation.json")
    assert result["status"] == "failed" and "platon" in result["error"]
    assert result["progress"] == "依赖修复未完成"


def test_host_managed_adapter_is_rechecked_for_upgrade_even_if_old_clean_probe_passes(host, monkeypatch):
    from argus.core import platon_windows

    resources = pm.install_root(host) / "crystalpilot/resources"
    old = resources / "software/old/platon-headless.exe"
    new = resources / "software/new/platon-headless.exe"
    pm.write_json(resources / "software.json", {"platon": {"path": str(old)}})
    pm.write_json(resources / "host-platon-runtime.json", {"path": str(old)})
    monkeypatch.setattr(platon_windows, "probe", lambda *a, **k: pytest.fail("Do not bypass adapter version validation"))
    calls = []
    def prepare(root, **kwargs):
        calls.append((root, kwargs["accept_license"]))
        return new
    monkeypatch.setattr(platon_windows, "prepare", prepare)
    assert pm._prepare_platon_for_setup(host) == new
    assert calls == [(resources, True)]


def test_a_working_user_supplied_platon_is_not_replaced_by_the_host_recipe(host, monkeypatch):
    from argus.core import platon_windows

    resources = pm.install_root(host) / "crystalpilot/resources"
    existing = host / "user-tool/platon.exe"
    pm.write_json(resources / "software.json", {"platon": {"path": str(existing)}})
    probed = []
    monkeypatch.setattr(platon_windows, "probe", lambda executable, **kw: probed.append(executable))
    monkeypatch.setattr(platon_windows, "prepare", lambda *a, **k: pytest.fail("Keep the working user installation"))
    assert pm._prepare_platon_for_setup(host) == existing
    assert probed == [existing]


@pytest.mark.parametrize("action", ["install", "update", "repair"])
def test_normal_actions_require_consent_and_keep_the_original_action(host, monkeypatch, action):
    spec = pm.catalog()["crystalpilot"]
    if action != "install":
        release = "crystalpilot/releases/previous"
        pm.write_json(pm.install_root(host) / "registry.json", {"crystalpilot": {
            "version": "previous", "release": release, "enabled": False, "python": "fixture-python",
        }})
        pm.write_json(pm.install_root(host) / release / "plugin.json", spec)
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **k: None)
    monkeypatch.setattr(pm, "compatibility", lambda *a, **k: {"supported": True})
    jobs = []
    def start(root, name, operation, target, args):
        jobs.append(operation)
        return {"status": "running"}
    monkeypatch.setattr(pm, "_start_job", start)
    with pytest.raises(pm.PluginError, match="许可"):
        pm.mutate("crystalpilot", action, host)
    assert not jobs
    assert pm.mutate("crystalpilot", action, host, payload={"accept_software_license": True}) == {"status": "running"}
    assert jobs == [action]


def test_failed_configure_marks_previous_health_as_stale_without_rewriting_it(host, monkeypatch):
    resources = pm.install_root(host) / "crystalpilot/resources"
    health = {"checked": 10, "ready": True, "components": []}
    pm.write_json(resources / "health.json", health)
    pm.write_json(resources.parent / "operation.json", {
        "status": "failed", "action": "repair", "started": 20, "completed": 30,
        "error": "PLATON candidate registration failed",
    })
    monkeypatch.setattr(pm, "compatibility", lambda *a, **k: {"supported": True})
    result = next(row for row in pm.plugin_rows(host) if row["id"] == "crystalpilot")
    assert result["health"]["stale"] is True
    assert result["operation"]["error"] == "PLATON candidate registration failed"
    assert pm.read_json(resources / "health.json") == health
    pm.write_json(resources / "health.json", {**health, "checked": 40})
    assert pm.plugin_rows(host)[0]["health"]["stale"] is False
