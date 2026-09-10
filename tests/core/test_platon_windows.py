"""Official Windows runtime preparation without installer or model execution."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest

from argus_skill.core import platon_windows as platon


def pe(machine=0x14C):
    data = bytearray(72)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    return bytes(data)


@pytest.mark.parametrize("accepted", [False, None, 1, "true"])
def test_license_is_explicit_and_checked_before_download(tmp_path, monkeypatch, accepted):
    monkeypatch.setattr(platon, "download", lambda *a, **k: pytest.fail("No download before consent"))
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="许可"):
        platon.prepare(root, accept_license=accepted)
    assert not root.exists()


@pytest.mark.parametrize("name", ["../platon.exe", "/platon.exe", "C:/platon.exe"])
def test_zip_paths_cannot_escape(tmp_path, name):
    archive = tmp_path / "program.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, pe())
    with pytest.raises(ValueError, match="不安全"):
        platon._member(archive, "platon.exe")


def test_duplicate_or_oversized_zip_members_are_rejected(tmp_path):
    archive = tmp_path / "program.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("one/platon.exe", pe())
        package.writestr("two/platon.exe", pe())
    with pytest.raises(ValueError, match="唯一"):
        platon._member(archive, "platon.exe")
    with pytest.raises(ValueError, match="过大"):
        platon._member(archive, "platon.exe", limit=1)


def test_pe_machine_and_corruption_are_checked():
    assert platon._machine(pe()) == 0x14C
    assert platon._machine(pe(0x8664)) == 0x8664
    for data in [b"", b"MZ", b"x" * 100]:
        with pytest.raises(ValueError):
            platon._machine(data)


def test_probe_requires_a_generated_rule_file(tmp_path, monkeypatch):
    monkeypatch.setattr(platon, "run", lambda *a, **k: "no work")
    with pytest.raises(RuntimeError, match="校验规则"):
        platon.probe(tmp_path / "platon.exe")


@pytest.fixture
def fake_publishers(tmp_path, monkeypatch):
    program = pe()
    launcher = tmp_path / "first-party-launcher.exe"
    launcher.write_bytes(pe(0x8664))
    monkeypatch.setattr(platon, "launcher_path", lambda: launcher)
    library = pe() + b"synthetic runtime"
    monkeypatch.setattr(platon, "DLL_SHA256", hashlib.sha256(library).hexdigest())
    archives = {}
    for url, name, data in [
        (platon.PLATON_URL, "platon.exe", program),
        (platon.TASKBAR_URL, "setup.exe", b"installer is input data only"),
        (platon.EXTRACTOR_URL, "innoextract.exe", b"synthetic extractor"),
    ]:
        archive = tmp_path / f"archive-{len(archives)}.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(name, data)
        archives[url] = archive
    downloads, commands = [], []
    def download(url, destination, *, checksum):
        assert checksum and url.startswith("https://")
        downloads.append(url)
        return archives[url]
    def run(command, **kwargs):
        commands.append([str(value) for value in command])
        assert Path(command[0]).name != "setup.exe"
        if Path(command[0]).name == "innoextract.exe":
            target = Path(command[command.index("--output-dir") + 1]) / "app"
            target.mkdir(parents=True)
            (target / "salflibc.dll").write_bytes(library)
        else:
            assert command[1:] == ["-z2"]
            (Path(kwargs["cwd"]) / "check.def").write_text("synthetic rules")
        return ""
    monkeypatch.setattr(platon, "download", download)
    monkeypatch.setattr(platon, "run", run)
    return downloads, commands


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only installer preparation")
def test_prepare_checks_then_publishes_and_reuses_verified_installation(tmp_path, fake_publishers):
    root = tmp_path / "resources"
    executable = platon.prepare(root, accept_license=True, progress=lambda _: None)
    assert executable.is_file() and (executable.parent / "salflibc.dll").is_file()
    marker = json.loads((root / "host-platon-runtime.json").read_text())
    assert marker["path"] == str(executable)
    assert platon.prepare(root, accept_license=True, progress=lambda _: None) == executable
    assert len(list((root / "software").iterdir())) == 1
    assert len([command for command in fake_publishers[1] if Path(command[0]).name == "innoextract.exe"]) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only installer preparation")
def test_failed_probe_never_replaces_existing_installation(tmp_path, fake_publishers, monkeypatch):
    root = tmp_path / "resources"
    root.mkdir()
    old = root / "keep.txt"
    old.write_text("existing software")
    marker = root / "host-platon-runtime.json"
    marker.write_text('{"path": "not-an-installation"}')
    before = marker.read_bytes()
    monkeypatch.setattr(platon, "probe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe failed")))
    with pytest.raises(RuntimeError, match="probe failed"):
        platon.prepare(root, accept_license=True, progress=lambda _: None)
    assert old.read_text() == "existing software"
    assert marker.read_bytes() == before
    assert not list((root / "software").iterdir())
