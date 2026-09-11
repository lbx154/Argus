import zipfile

import portalocker
import pytest

from argus_skill.core import plugin_manager as pm


@pytest.fixture
def empty_host(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path / "host"))
    for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER"):
        monkeypatch.setenv("ARGUS_SKILL_" + role + "_BACKEND", "pi")
    return tmp_path / "host"


def test_default_does_not_load_optional_vertical(empty_host):
    from argus_skill.skills.vertical_select import available_verticals

    assert pm.installed(empty_host) == {}
    assert "crystalpilot" not in available_verticals()
    row = pm.plugin_rows(empty_host)[0]
    assert not row["installed"] and not row["available"]


@pytest.mark.parametrize("backend", ["codex", "copilot", "pi"])
@pytest.mark.parametrize("system", ["linux", "windows", "darwin"])
def test_supported_matrix(backend, system, monkeypatch):
    from types import SimpleNamespace

    from argus_skill.core import role_config

    # Simulate the target architecture instead of inheriting arm64 on macOS CI.
    monkeypatch.setattr(pm.platform, "machine", lambda: "x86_64")

    monkeypatch.setattr(
        role_config,
        "resolve_all_roles",
        lambda **kw: [SimpleNamespace(role="engineer", backend=backend)],
    )
    assert pm.compatibility(pm.catalog()["crystalpilot"], system=system)["supported"]


def test_unsupported_mixed_roles_are_reported(monkeypatch):
    from types import SimpleNamespace

    from argus_skill.core import role_config

    monkeypatch.setattr(
        role_config,
        "resolve_all_roles",
        lambda **kw: [
            SimpleNamespace(role="engineer", backend="pi"),
            SimpleNamespace(role="reviewer", backend="claude"),
        ],
    )
    result = pm.compatibility(pm.catalog()["crystalpilot"])
    assert not result["supported"] and result["unsupported_roles"] == {"reviewer": "claude"}
    assert "敬请期待" in result["reason"]


def test_hash_mismatch_is_rejected(tmp_path):
    source = tmp_path / "release.whl"
    source.write_bytes(b"bad payload")
    spec = {"_catalog_dir": str(tmp_path), "artifact": {"sha256": "0" * 64, "local": "release.whl"}}
    with pytest.raises(pm.PluginError, match="校验失败"):
        pm._fetch(spec, tmp_path / "copy.whl")


def test_archive_traversal_is_rejected(tmp_path):
    wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("../outside.py", "bad")
    with pytest.raises(pm.PluginError, match="Unsafe path"):
        pm._extract(wheel, tmp_path / "package")
    assert not (tmp_path / "outside.py").exists()


def test_manage_requires_auth_and_does_not_launch_missing_plugin(empty_host):
    from fastapi.testclient import TestClient

    from argus_skill.webapi.server import create_app

    client = TestClient(create_app(global_root=empty_host, auth_token="test"))
    assert client.post("/api/plugins/crystalpilot/manage/install").status_code == 401
    assert client.get("/plugins/crystalpilot/").status_code == 404
    assert (
        client.post(
            "/api/plugins/crystalpilot/launch", headers={"Authorization": "Bearer test"}
        ).status_code
        == 404
    )


def test_unsupported_primary_is_blocked_even_with_supported_roles(monkeypatch):
    from types import SimpleNamespace

    from argus_skill.core import role_config

    monkeypatch.setattr(
        role_config,
        "resolve_all_roles",
        lambda **kw: [SimpleNamespace(role="engineer", backend="pi")],
    )
    result = pm.compatibility(
        pm.catalog()["crystalpilot"], env={"ARGUS_SKILL_RUNNER_BACKEND": "claude"}
    )
    assert not result["supported"] and result["unsupported_roles"] == {"default": "claude"}


def test_auxiliary_backend_can_follow_role_instead_of_global(monkeypatch, empty_host):
    from argus_skill.adapters.agent_cli_backend import build_agent_cli_backend_from_env
    from argus_skill.agent_cli import runner_backend

    monkeypatch.setattr(
        runner_backend, "resolve_available_runner",
        lambda backend, configured=None: (backend, configured or f"/test/{backend}"),
    )
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "pi")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "codex")
    assert build_agent_cli_backend_from_env(role="engineer").backend == "codex"
    assert build_agent_cli_backend_from_env().backend == "pi"


def test_python_falls_back_when_host_interpreter_is_incompatible(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.delenv("ARGUS_PLUGIN_PYTHON", raising=False)
    monkeypatch.setattr(
        pm.shutil, "which", lambda name: "/bin/" + name if name == "python3.11" else None
    )

    def probe(command, **kwargs):
        if command[0] == "python3.11":
            return SimpleNamespace(stdout="/compatible/python3.11\n")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(pm.subprocess, "run", probe)
    assert pm._python() == "/compatible/python3.11"


def test_frozen_python_compatibility_shim_is_not_an_installation_python(monkeypatch):
    import subprocess
    import sys

    real_run = subprocess.run
    monkeypatch.setenv("ARGUS_PLUGIN_PYTHON", sys.executable)

    def frozen_probe(command, **kwargs):
        command = [*command[:-1], "import sys; sys.frozen = True; " + command[-1]]
        return real_run(command, **kwargs)

    monkeypatch.setattr(pm.subprocess, "run", frozen_probe)
    with pytest.raises(pm.PluginError, match="Python"):
        pm._python()


def test_selected_python_can_create_a_real_scientific_environment(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("ARGUS_PLUGIN_PYTHON", sys.executable)
    python = pm._python()
    environment = tmp_path / "science"
    subprocess.run([python, "-m", "venv", str(environment)], check=True, timeout=60)
    executable = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [str(executable), "-I", "-c", "import sys,pip; assert not getattr(sys,'frozen',False)"],
        check=True, timeout=15,
    )


def test_native_plugin_requires_this_sessions_enabled_binding(empty_host, monkeypatch):
    from types import SimpleNamespace

    from argus_skill.core.workbench_plugins import prepare_plugin_run

    shared = empty_host / "shared"
    first, second = shared / "state/s-first", shared / "state/s-second"
    rows = {
        first.name: {"sid": first.name, "life_dir": str(first), "enabled": True},
        second.name: {"sid": second.name, "life_dir": str(second), "enabled": False},
    }
    path = empty_host / pm.catalog()["crystalpilot"]["session_bindings"]
    pm.write_json(path, rows)
    (pm.install_root(empty_host) / "crystalpilot").mkdir(parents=True)
    prepared = []

    def prepare(prompt, options, **kwargs):
        prepared.append(prompt)
        return prompt + " plugin enabled", options

    plugin = SimpleNamespace(owns_workdir=lambda _path: True, prepare_run=prepare)
    monkeypatch.setattr(pm, "installed", lambda root=None: {"crystalpilot": plugin})
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **k: plugin)
    options = SimpleNamespace(working_dir=shared)
    with portalocker.Lock(str(pm.install_root(empty_host) / "crystalpilot/manage.lock"), timeout=0):
        for project in (None, second, shared / "state/s-unbound"):
            assert prepare_plugin_run(
                "ordinary task", options, backend="pi", run_label="engineer",
                project_root=project,
            ) == ("ordinary task", options)
    assert prepared == []
    assert prepare_plugin_run(
        "bound task", options, backend="pi", run_label="engineer", project_root=first,
    ) == ("bound task plugin enabled", options)
    assert prepared == ["bound task"]
    rows[first.name]["enabled"] = False
    pm.write_json(path, rows)
    assert prepare_plugin_run(
        "after disabling", options, backend="pi", run_label="engineer", project_root=first,
    ) == ("after disabling", options)


def test_workbench_daemon_keeps_the_plugin_installation_root(empty_host, monkeypatch):
    import os
    from types import SimpleNamespace

    from argus_skill.daemon import _life_worker_boot
    from argus_skill.daemon.life_worker import LifeWorker
    from argus_skill.tools import capability_vault

    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)
    workbench = empty_host / "plugins/crystalpilot/workbench"
    life = workbench / "projects/s-crystalpilot-test"
    plugin = SimpleNamespace(accounting_root=lambda path: empty_host if path == life else None)
    monkeypatch.setattr(pm, "installed", lambda root=None: {"crystalpilot": plugin})
    monkeypatch.setattr(_life_worker_boot, "configure_framework_python_env", lambda **kw: None)
    monkeypatch.setattr(capability_vault, "gpu_env_vars", lambda: {})
    worker = SimpleNamespace(
        config=SimpleNamespace(global_root=workbench, life_dir=life, project_workdir=None),
        _install_signal_handlers=lambda: None,
        _rf_export_configured_backend=lambda: None,
    )

    LifeWorker._rf_bootstrap_environment(worker)

    assert os.environ["ARGUS_SKILL_HOME"] == str(workbench)
    assert pm.host_root() == empty_host
