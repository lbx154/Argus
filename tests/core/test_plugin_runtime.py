import hashlib

import httpx
import pytest

from argus_skill.core import plugin_runtime as runtime


def client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        runtime.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )


def test_verified_download_keeps_existing_file_on_mismatch(tmp_path, monkeypatch):
    target = tmp_path / "program"
    target.write_bytes(b"previous")
    client(monkeypatch, lambda request: httpx.Response(200, content=b"corrupt"))
    with pytest.raises(ValueError, match="SHA-256"):
        runtime.download(
            "https://example.org/program", target, checksum=hashlib.sha256(b"valid").hexdigest()
        )
    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.part"))


def test_license_redirect_never_forwards_credentials(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/program"})

    client(monkeypatch, handler)
    with pytest.raises(ValueError, match="授权凭据"):
        runtime.download(
            "https://licensed.example/file", tmp_path / "out", auth=("academic", "secret")
        )
    assert len(requests) == 1
    assert requests[0].url.host == "licensed.example"


def test_download_limits_and_tls_downgrade(tmp_path, monkeypatch):
    client(monkeypatch, lambda request: httpx.Response(200, content=b"a" * 32))
    with pytest.raises(ValueError, match="大小限制"):
        runtime.download("https://example.org/file", tmp_path / "out", limit=16)
    assert not (tmp_path / "out").exists()
    with pytest.raises(ValueError, match="HTTPS"):
        runtime.download("http://example.org/file", tmp_path / "out")


@pytest.mark.parametrize(
    "system,machine,key",
    [
        ("Windows", "AMD64", "win-64"),
        ("Linux", "x86_64", "linux-64"),
        ("Darwin", "x86_64", "osx-64"),
        ("Darwin", "arm64", "osx-arm64"),
    ],
)
def test_platform_artifacts(system, machine, key):
    assert runtime.platform_key(system, machine) == key


def test_unavailable_architecture_is_not_misidentified():
    with pytest.raises(ValueError, match="发行包"):
        runtime.platform_key("Linux", "aarch64")
    with pytest.raises(ValueError):
        runtime.platform_key("Linux", "riscv64")


def test_installer_does_not_inherit_model_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "secret")
    monkeypatch.setenv("CODEX_HOME", "/external/config")
    monkeypatch.setenv("PYTHONPATH", "/unrelated/module")
    monkeypatch.setenv("CONDA_PREFIX", "/external/conda")
    env = runtime.clean_env()
    assert not {
        "OPENAI_API_KEY",
        "COPILOT_PROVIDER_API_KEY",
        "CODEX_HOME",
        "PYTHONPATH",
        "CONDA_PREFIX",
    } & set(env)


def test_missing_host_python_is_provisioned_locally(monkeypatch, tmp_path):
    import subprocess

    from argus_skill.core import plugin_manager

    monkeypatch.delenv("ARGUS_PLUGIN_PYTHON", raising=False)
    monkeypatch.setattr(plugin_manager.shutil, "which", lambda name: None)

    def reject(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(plugin_manager.subprocess, "run", reject)
    roots = []
    monkeypatch.setattr(
        runtime, "portable_python", lambda root: roots.append(root) or "/local/python"
    )
    assert plugin_manager._python(tmp_path) == "/local/python"
    assert roots == [tmp_path / "extensions/runtime"]


def test_timed_out_installer_reaps_its_child(tmp_path):
    import json
    import subprocess
    import sys

    import psutil

    marker = tmp_path / "child.json"
    source = (
        "import subprocess,sys,time,json; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"Path({str(marker)!r}).write_text(json.dumps(p.pid)); time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runtime.run([sys.executable, "-c", source], timeout=1)
    assert marker.exists()
    child = json.loads(marker.read_text())
    try:
        assert psutil.Process(child).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass  # The OS can reap the killed child between two status queries.
