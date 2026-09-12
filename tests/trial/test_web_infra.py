import asyncio
import os
import socket

import pytest

from argus_skill.trial.egress import public_addresses
from argus_skill.trial.web_admin import initialize, prepare_tenant_directory


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "172.17.0.1", "192.168.1.1", "169.254.169.254",
    "::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1",
])
def test_egress_rejects_nonpublic_addresses(monkeypatch, address):
    async def exercise():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(ValueError, match="Private"):
            await public_addresses("untrusted.example")
    asyncio.run(exercise())


def test_egress_pins_only_validated_public_address(monkeypatch):
    async def exercise():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        assert await public_addresses("example.org") == [(socket.AF_INET, "93.184.215.14")]
    asyncio.run(exercise())


def test_initialization_preserves_keys_balances_and_tenant_tokens(tmp_path):
    import json

    from argus_skill.trial.store import Store

    token = tmp_path / "admin-token"
    token.write_text("not-a-real-admin-token")
    root = tmp_path / "private"
    initialize(root, token, "http://127.0.0.1:8896")
    analytics_path = root / "analytics.json"
    analytics = json.loads(analytics_path.read_text())
    assert len(analytics["tenants"]) == 10
    assert all(tenant["internal_test"] is True for tenant in analytics["tenants"].values())
    analytics["tenants"]["trial-05"]["internal_test"] = False  # Explicit later external trial.
    analytics_path.write_text(json.dumps(analytics))
    configured_analytics = analytics_path.read_bytes()
    exported = (root / "invitations.json").read_bytes()
    portal = (root / "portal.json").read_bytes()
    store = Store(root / "meter/usage.sqlite3")
    request = store.reserve("trial-01", 500)
    store.settle(request, 123)
    initialize(root, token, "http://127.0.0.1:8896")
    assert (root / "invitations.json").read_bytes() == exported
    assert (root / "portal.json").read_bytes() == portal
    assert analytics_path.read_bytes() == configured_analytics
    assert len(json.loads(exported)) == 10
    assert store.status("trial-01")["tokens_used"] == 123
    assert store.status("trial-02")["tokens_used"] == 0
    assert (root / "invitations.json").stat().st_mode & 0o077 == 0
    config = json.loads((root / "compute.json").read_text())
    assert config["gpu_hours_per_tenant"] == 200
    assert config["gpu_devices"] == [0, 1, 2, 3]
    assert len({v["data_dir"] for v in config["tenants"].values()}) == 10
    assert json.loads(portal)["token_limit"] is None


def test_web_quota_increase_keeps_usage_and_enforces_exact_new_boundary(tmp_path):
    from argus_skill.trial.store import Store, TrialError

    path = tmp_path / "usage.sqlite3"
    original = Store(path)
    original.issue("trial-01", "test-invitation")
    request = original.reserve("trial-01", 850_000)
    original.settle(request, 850_000)
    expanded = Store(path, token_limit=10_000_000)
    assert expanded.authenticate("test-invitation") == "trial-01"
    assert expanded.status("trial-01")["tokens_remaining"] == 9_150_000
    request = expanded.reserve("trial-01", 9_150_000)
    expanded.settle(request, 9_150_000)
    restarted = Store(path, token_limit=10_000_000)
    assert restarted.status("trial-01")["tokens_remaining"] == 0
    with pytest.raises(TrialError, match="Insufficient") as error:
        restarted.reserve("trial-01", 1)
    assert error.value.status == 402
    desktop = Store(tmp_path / "desktop.sqlite3")
    desktop.issue("trial-01", "separate-desktop-key")
    assert desktop.status("trial-01")["token_limit"] == 1_000_000


@pytest.mark.parametrize("entry", ["home", "workspace", ".tenant-volume"])
def test_root_initialization_rejects_tenant_symlinks(tmp_path, entry):
    tenant = tmp_path / "tenant"
    tenant.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tenant / entry).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        prepare_tenant_directory(tenant, "trial-01", os.getuid(), os.getgid())
    assert list(outside.iterdir()) == []


def test_root_initialization_keeps_writes_anchored_during_replacement(tmp_path, monkeypatch):
    tenant, outside = tmp_path / "tenant", tmp_path / "outside"
    tenant.mkdir()
    outside.mkdir()
    original_open = os.open

    def replace_after_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == "workspace":
            (tenant / "workspace").rename(tenant / "original-workspace")
            (tenant / "workspace").symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_open)
    prepare_tenant_directory(tenant, "trial-01", os.getuid(), os.getgid())
    assert list(outside.iterdir()) == []
    assert (tenant / "original-workspace/AGENTS.md").is_file()


def test_root_initialization_rejects_nonregular_identity_without_blocking(tmp_path):
    os.mkfifo(tmp_path / ".tenant-volume")
    with pytest.raises(ValueError, match="regular file"):
        prepare_tenant_directory(tmp_path, "trial-01", os.getuid(), os.getgid())


@pytest.mark.parametrize("image", [None, "argus-web-trial:operator-release"])
@pytest.mark.parametrize("existing", [False, True])
def test_start_cli_selects_capture_image_only_for_new_containers(tmp_path, monkeypatch, image, existing):
    import json
    import sys
    from subprocess import CompletedProcess

    from argus_skill.trial import web_admin

    commands = []

    def docker(argv, **kwargs):
        commands.append(argv)
        if argv[:3] == ["docker", "container", "inspect"]:
            if existing:
                tenant = argv[-1].removeprefix("argus-web-")
                data = [{"Config": {"Labels": {"argus.web.tenant": tenant}}}]
                return CompletedProcess(argv, 0, stdout=json.dumps(data), stderr="")
            return CompletedProcess(argv, 1, stdout="", stderr="No such container")
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(web_admin.subprocess, "run", docker)
    monkeypatch.setattr(web_admin.os.path, "ismount", lambda path: True)
    argv = ["web-admin", "start-containers", "--root", str(tmp_path)]
    if image is not None:
        argv += ["--image", image]
    monkeypatch.setattr(sys, "argv", argv)
    previous_mask = os.umask(0o077)
    try:
        web_admin.main()
    finally:
        os.umask(previous_mask)

    launches = [command for command in commands if command[:2] == ["docker", "run"]]
    starts = [command for command in commands if command[:2] == ["docker", "start"]]
    if existing:
        assert launches == []
        assert starts == [["docker", "start", f"argus-web-trial-{number:02d}"] for number in range(1, 11)]
    else:
        assert starts == []
        assert len(launches) == 10
        expected = image or "argus-web-trial:pi-data-20260911-r6"
        assert all(command[-1] == expected for command in launches)
        for number, command in enumerate(launches, 1):
            assert f"type=bind,src={tmp_path}/tenants/trial-{number:02d}/data,dst=/tenant" in command
    assert not any(command[1] in {"stop", "restart", "rm"} for command in commands)


def _fake_docker(state):
    """A stateful docker stand-in tracking each container's image and label."""
    import json
    from subprocess import CompletedProcess

    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        head = argv[:3]
        if head == ["docker", "container", "inspect"]:
            name = argv[-1]
            if name not in state:
                return CompletedProcess(argv, 1, stdout="", stderr="No such container")
            record = state[name]
            data = [{"Config": {"Image": record["image"],
                                "Labels": {"argus.web.tenant": record["label"]}}}]
            return CompletedProcess(argv, 0, stdout=json.dumps(data), stderr="")
        if argv[:2] == ["docker", "run"]:
            name = argv[argv.index("--name") + 1]
            label = argv[argv.index("--label") + 1].split("=", 1)[1]
            state[name] = {"image": argv[-1], "label": label}
        elif argv[:2] == ["docker", "rename"]:
            state[argv[-1]] = state.pop(argv[-2])
        elif argv[:2] == ["docker", "rm"]:
            state.pop(argv[-1], None)
        return CompletedProcess(argv, 0, stdout="", stderr="")

    return run, commands


def test_roll_recreates_only_containers_on_a_different_image(tmp_path, monkeypatch):
    from argus_skill.trial import web_admin

    state = {f"argus-web-trial-{n:02d}": {"image": "argus:old", "label": f"trial-{n:02d}"}
             for n in range(1, 11)}
    # One tenant is already on the target image and must be left untouched.
    state["argus-web-trial-05"]["image"] = "argus:new"
    run, commands = _fake_docker(state)
    monkeypatch.setattr(web_admin.subprocess, "run", run)
    monkeypatch.setattr(web_admin.os.path, "ismount", lambda path: True)

    results = web_admin.roll_containers(tmp_path, image="argus:new")

    assert results["trial-05"] == "current"
    assert all(results[f"trial-{n:02d}"] == "upgraded" for n in range(1, 11) if n != 5)
    # Every rolled tenant drained, kept a rollback, and relaunched on the new image.
    assert ["docker", "stop", "--time", "30", "argus-web-trial-05"] not in commands
    for n in range(1, 11):
        if n == 5:
            continue
        name = f"argus-web-trial-{n:02d}"
        assert ["docker", "stop", "--time", "30", name] in commands
        assert ["docker", "rename", name, f"{name}-rollback"] in commands
    launches = [c for c in commands if c[:2] == ["docker", "run"]]
    assert len(launches) == 9 and all(c[-1] == "argus:new" for c in launches)
    # Every container, new and pre-existing, still exists afterwards on the new image.
    assert all(state[f"argus-web-trial-{n:02d}"]["image"] == "argus:new" for n in range(1, 11))
    assert all(f"argus-web-trial-{n:02d}-rollback" in state for n in range(1, 11) if n != 5)


def test_roll_restores_previous_container_when_recreate_fails(tmp_path, monkeypatch):
    from subprocess import CalledProcessError

    from argus_skill.trial import web_admin

    state = {"argus-web-trial-01": {"image": "argus:old", "label": "trial-01"}}
    run, commands = _fake_docker(state)

    def failing(argv, **kwargs):
        if argv[:2] == ["docker", "run"]:
            raise CalledProcessError(1, argv)
        return run(argv, **kwargs)

    monkeypatch.setattr(web_admin.subprocess, "run", failing)
    monkeypatch.setattr(web_admin.os.path, "ismount", lambda path: True)

    with pytest.raises(CalledProcessError):
        web_admin.roll_containers(tmp_path, numbers=[1], image="argus:new")

    # The drained container is renamed back and restarted; service is preserved.
    assert ["docker", "rename", "argus-web-trial-01-rollback", "argus-web-trial-01"] in commands
    assert ["docker", "start", "argus-web-trial-01"] in commands
    assert state["argus-web-trial-01"]["image"] == "argus:old"
    assert "argus-web-trial-01-rollback" not in state


def test_release_rolls_backend_before_flipping_frontend_and_writes_manifest(tmp_path, monkeypatch):
    import json
    from subprocess import CompletedProcess

    from argus_skill.trial import web_admin

    root = tmp_path / "root"
    root.mkdir()
    (root / "portal.json").write_text(json.dumps({"frontend_dir": "/old/dist", "tenants": {}}))
    source = tmp_path / "src"
    dist = source / "frontend/web/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")

    state = {f"argus-web-trial-{n:02d}": {"image": "argus:old", "label": f"trial-{n:02d}"}
             for n in range(1, 11)}
    docker, commands = _fake_docker(state)

    def run(argv, **kwargs):
        if argv[:2] == ["git", "-C"]:
            out = "abc1234" if argv[3] == "rev-parse" else ""
            return CompletedProcess(argv, 0, stdout=out + "\n", stderr="")
        return docker(argv, **kwargs)

    monkeypatch.setattr(web_admin.subprocess, "run", run)
    monkeypatch.setattr(web_admin.os.path, "ismount", lambda path: True)

    manifest = web_admin.release(root, image="argus:new", source=source)

    assert manifest["version"] == "abc1234"
    assert manifest["image"] == "argus:new"
    assert manifest["frontend_dir"] == str(dist)
    assert set(manifest["tenants"].values()) == {"upgraded"}
    saved = json.loads((root / "release.json").read_text())
    assert saved == manifest
    # Frontend flips only after the backend rolled: portal.json now points at the
    # new dist and the portal restart is the final action, after the image roll.
    assert json.loads((root / "portal.json").read_text())["frontend_dir"] == str(dist)
    restart = ["systemctl", "--user", "restart", "argus-web-trial-portal"]
    assert commands[-1] == restart
    last_run = max(i for i, c in enumerate(commands) if c[:2] == ["docker", "run"])
    assert last_run < commands.index(restart)
