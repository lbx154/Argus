import zipfile

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


@pytest.mark.parametrize("value", ["true", 1, None])
def test_host_software_consent_is_typed_and_authenticated(empty_host, monkeypatch, value):
    from fastapi.testclient import TestClient

    from argus_skill.webapi.server import create_app

    monkeypatch.setattr(pm, "mutate", lambda *a, **k: pytest.fail("invalid consent must not reach a job"))
    client = TestClient(create_app(global_root=empty_host, auth_token="test"))
    route = "/api/plugins/crystalpilot/manage/platon_runtime"
    assert client.post(route, json={"accept_software_license": True}).status_code == 401
    assert client.post(route, json={"accept_software_license": value},
                       headers={"Authorization": "Bearer test"}).status_code == 409


def test_host_runtime_uses_installed_plugin_contract_and_needs_consent(empty_host, monkeypatch):
    import os
    if os.name != "nt":
        pytest.skip("Windows host operation")
    release = "crystalpilot/releases/test"
    spec = pm.catalog()["crystalpilot"]
    pm.write_json(pm.install_root(empty_host) / release / "plugin.json", spec)
    pm.write_json(pm.install_root(empty_host) / "registry.json", {
        "crystalpilot": {"release": release, "enabled": False, "python": "fixture-python"},
    })
    monkeypatch.setattr(pm, "load_plugin", lambda *a, **kw: None)
    jobs = []
    def start(root, name, action, target, args):
        jobs.append((action, args))
        return {"status": "running"}
    monkeypatch.setattr(pm, "_start_job", start)
    with pytest.raises(pm.PluginError, match="许可"):
        pm.mutate("crystalpilot", "platon_runtime", empty_host)
    assert not jobs
    assert pm.mutate("crystalpilot", "platon_runtime", empty_host,
                     payload={"accept_software_license": True})["status"] == "running"
    assert jobs[0][0] == "platon_runtime"
    assert jobs[0][1][1]["setup"]["module"] == spec["setup"]["module"]
