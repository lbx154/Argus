from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from argus_skill.trial import client, desktop, native_cli


@pytest.mark.parametrize("system,machine,asset", [
    ("Darwin", "arm64", "copilot-darwin-arm64.tar.gz"),
    ("Darwin", "x86_64", "copilot-darwin-x64.tar.gz"),
    ("Windows", "AMD64", "copilot-win32-x64.zip"),
    ("Windows", "ARM64", "copilot-win32-arm64.zip"),
])
def test_native_assets_cover_both_desktop_platforms(system, machine, asset):
    name, digest = native_cli.asset_for(system, machine)
    assert name == asset and len(digest) == 64


def test_no_download_or_profile_changes_for_invalid_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    previous = b"previous-profile"
    client.profile_path().write_bytes(previous)
    monkeypatch.setattr(native_cli, "install_native_copilot", lambda: pytest.fail("Invalid key must not install"))
    with pytest.raises(ValueError, match="Invalid trial key"):
        client.setup_trial("https://argusbot.cn", api_key="bad-key", desktop=True)
    assert client.profile_path().read_bytes() == previous


def archive_bytes():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as package:
        entry = tarfile.TarInfo("copilot")
        entry.size = len(b"executable")
        package.addfile(entry, io.BytesIO(b"executable"))
        # No arbitrary archive path is ever extracted.
        entry = tarfile.TarInfo("../escape")
        entry.size = 3
        package.addfile(entry, io.BytesIO(b"bad"))
    return data.getvalue()


def test_install_checks_integrity_and_only_publishes_verified_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setattr(native_cli.platform, "system", lambda: "Darwin")
    content = archive_bytes()
    monkeypatch.setattr(native_cli, "asset_for", lambda *_: ("copilot.tar.gz", hashlib.sha256(content).hexdigest()))
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(content))
    verified = []
    monkeypatch.setattr(native_cli, "verify_cli", lambda path: verified.append(path.read_bytes()))
    path = Path(native_cli.install_native_copilot())
    assert path.read_bytes() == b"executable" and verified == [b"executable"]
    assert not list(tmp_path.rglob("escape"))
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("Reuse installed CLI"))
    assert native_cli.install_native_copilot() == str(path)


def test_corrupt_download_never_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"corrupt"))
    monkeypatch.setattr(native_cli, "verify_cli", lambda *_: pytest.fail("Unverified bytes must not execute"))
    with pytest.raises(ValueError, match="校验失败"):
        native_cli.install_native_copilot()
    assert not any(path.is_file() for path in tmp_path.rglob("copilot.exe" if os.name == "nt" else "copilot"))


def test_desktop_protocol_never_relays_key_or_setup_stdout(monkeypatch):
    from argus_skill.core import knob_store

    key = "argus_trial_" + "a" * 64
    monkeypatch.setattr(desktop.sys, "stdin", io.StringIO(json.dumps({"api_key": key}) + "\n"))
    wire = io.BytesIO()
    # Match the frozen helper's redirected Windows stdout.
    monkeypatch.setattr(desktop.sys, "stdout", io.TextIOWrapper(wire, encoding="cp1252"))

    def setup(_url, **kwargs):
        assert kwargs["api_key"] == key and kwargs["desktop"]
        print(key)
        kwargs["progress"]("验证中")
        return 0

    monkeypatch.setattr(desktop, "setup_trial", setup)
    monkeypatch.setattr(knob_store, "read_persisted_knobs", lambda: {"ARGUS_SKILL_RUNNER_BIN": "/用户/copilot"})
    assert desktop.main() == 0
    output = wire.getvalue().decode("ascii")
    assert key not in output
    events = [json.loads(line) for line in output.splitlines()]
    assert events[-1] == {"event": "complete", "runner_bin": "/用户/copilot"}
    assert any(event.get("message") == "验证中" for event in events)


@pytest.mark.skipif(os.environ.get("ARGUS_TEST_NATIVE_COPILOT") != "1", reason="Native asset smoke is opt-in")
def test_actual_native_cli_needs_no_node_install(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    path = Path(native_cli.install_native_copilot())
    assert path.is_file()
    native_cli.verify_cli(path)
