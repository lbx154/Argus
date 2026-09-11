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
