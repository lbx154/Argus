"""Exercise Windows build ordering and preservation with subprocess stand-ins."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows backend build entry point")


def _builder(tmp_path: Path, monkeypatch):
    script = ROOT / "desktop-tauri/scripts/build-windows-backend.py"
    spec = importlib.util.spec_from_file_location("windows_backend_build_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    desktop = tmp_path / "desktop-tauri"
    desktop.mkdir()
    monkeypatch.setattr(module, "ROOT", desktop)
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "assert_release_versions", lambda repo: "0.1.7")
    monkeypatch.setattr(module.shutil, "which", lambda name: "node.exe")
    manifest = {"package_version": "0.1.7", "release_id": "0.1.7+fixture", "source_digest": "fixture"}
    (tmp_path / "argus").mkdir()
    (tmp_path / "argus/__init__.py").write_text('__version__ = "0.1.7"\n', encoding="utf-8")
    (tmp_path / "argus/release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Bind the optional repo argument explicitly when testing a synthetic tree.
    original_validate = module.validate_payload_identity
    monkeypatch.setattr(module, "validate_payload_identity", lambda source: original_validate(source, tmp_path))
    return module, desktop, manifest


def _frozen(desktop: Path, manifest: dict) -> Path:
    source = desktop / "build/argus-backend"
    (source / "_internal/argus").mkdir(parents=True)
    (source / "argus-backend.exe").write_bytes(b"test-only stand-in; never execute")
    package = source / "_internal/argus"
    (package / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "__init__.py").write_bytes((desktop.parent / "argus/__init__.py").read_bytes())
    for name, content in {
        "tui/bundle/argus.mjs": f'export const releaseId = "{manifest["release_id"]}";',
        "web/dist/index.html": '<script src="./assets/main.js"></script>',
        "web/dist/assets/main.js": f'export const releaseId = "{manifest["release_id"]}";',
    }.items():
        target = package / "_frontend" / name
        original = desktop.parent / "frontend" / name
        for path in (target, original):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return source


def test_windows_build_orders_identity_native_adapter_freeze_probes_and_staging(tmp_path, monkeypatch):
    builder, desktop, manifest = _builder(tmp_path, monkeypatch)
    commands = []

    def run(command, **kwargs):
        commands.append(tuple(command))
        assert kwargs["check"] is True
        assert kwargs["env"]["PYTHONPATH"] == str(tmp_path)
        if "PyInstaller" in command:
            _frozen(desktop, manifest)

    monkeypatch.setattr(builder.subprocess, "run", run)
    assert builder.main([]) == 0
    release = next(i for i, command in enumerate(commands) if "argus.release_tools.build_release" in command)
    native = next(i for i, command in enumerate(commands) if any(str(item).endswith("build-native-tools.ps1") for item in command))
    freeze = next(i for i, command in enumerate(commands) if "PyInstaller" in command)
    verify = next(i for i, command in enumerate(commands) if "--verify-frozen-runtime" in command)
    stage = next(i for i, command in enumerate(commands) if any(str(item).endswith("stage-backend.mjs") for item in command))
    assert release < native < freeze < verify < stage
    assert "--clean" not in commands[freeze]
    assert "--noconfirm" not in commands[freeze]
    assert any("ZoneInfo('Asia/Shanghai')" in item for command in commands for item in command)


def test_windows_build_refuses_existing_payload_before_running_any_command(tmp_path, monkeypatch):
    builder, desktop, manifest = _builder(tmp_path, monkeypatch)
    source = _frozen(desktop, manifest)
    before = (source / "argus-backend.exe").read_bytes()
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: pytest.fail("existing payload must not be rebuilt"))
    with pytest.raises(RuntimeError, match="old build files will not be deleted"):
        builder.main([])
    assert (source / "argus-backend.exe").read_bytes() == before


def test_failed_frontend_identity_build_never_freezes_an_old_cockpit(tmp_path, monkeypatch):
    builder, desktop, _manifest = _builder(tmp_path, monkeypatch)
    commands = []

    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(23, command)

    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        builder.main([])
    assert len(commands) == 1
    assert "argus.release_tools.build_release" in commands[0]
    assert not (desktop / "build/argus-backend").exists()


def test_prepare_rechecks_source_and_artifacts_without_refreezing(tmp_path, monkeypatch):
    builder, desktop, manifest = _builder(tmp_path, monkeypatch)
    _frozen(desktop, manifest)
    commands = []
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: commands.append(command))
    assert builder.main(["--prepare-only"]) == 0
    assert len(commands) == 3
    assert "argus.release_tools.generate_manifest" in commands[0]
    assert "--check" in commands[0]
    assert "argus.release_tools.check_artifacts" in commands[1]
    assert not any("PyInstaller" in command for command in commands)


def test_mixed_frozen_identity_is_rejected_before_staging(tmp_path, monkeypatch):
    builder, desktop, manifest = _builder(tmp_path, monkeypatch)
    _frozen(desktop, {**manifest, "source_digest": "different"})
    commands = []
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: commands.append(command))
    with pytest.raises(RuntimeError, match="identity differs"):
        builder.main(["--prepare-only"])
    assert not any(any(str(item).endswith("stage-backend.mjs") for item in command) for command in commands)


@pytest.mark.parametrize("relative,message", [
    ("__init__.py", "first-party source differs"),
    ("_frontend/tui/bundle/argus.mjs", "TUI bundle has a different release"),
    ("_frontend/web/dist/assets/main.js", "Web bundle has a different release"),
])
def test_frozen_byte_or_frontend_identity_mismatch_is_rejected(tmp_path, monkeypatch, relative, message):
    builder, desktop, manifest = _builder(tmp_path, monkeypatch)
    source = _frozen(desktop, manifest)
    (source / "_internal/argus" / relative).write_text("altered fixture", encoding="utf-8")
    with pytest.raises(RuntimeError, match=message):
        builder.validate_frozen_inputs(source, tmp_path)


def test_build_isolation_preserves_windows_bootstrap_without_ambient_account_configuration(tmp_path, monkeypatch):
    builder, _desktop, _manifest = _builder(tmp_path, monkeypatch)
    monkeypatch.setenv("EXAMPLE_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("ARGUS_SKILL_HOME", "unrelated-profile")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    work = tmp_path / "isolated"
    work.mkdir()
    env = builder.isolated_environment(work)
    assert "EXAMPLE_API_KEY" not in env
    assert env["ARGUS_SKILL_HOME"] == str(work / "state")
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["USERPROFILE"] == str(work / "home")
    assert env["APPDATA"] == str(work / "roaming")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["RUSTUP_TOOLCHAIN"] == "stable-x86_64-pc-windows-msvc"
