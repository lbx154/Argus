import json
import subprocess

import pytest

from argus_skill.trial import relay_guardian as module


@pytest.fixture
def guardian(tmp_path, monkeypatch):
    for number in range(1, 11):
        bootstrap = tmp_path / "tenants" / f"trial-{number:02}" / "bootstrap"
        bootstrap.mkdir(parents=True)
        (bootstrap / "runtime.json").write_text("{}")

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect_ex(self, path):
            return 0

    monkeypatch.setattr(module.socket, "socket", lambda *args: Socket())
    return module.Guardian(tmp_path)


def test_recovers_only_failed_owned_relays_once(guardian, monkeypatch):
    launched = []

    class Child:
        def __init__(self, argv):
            launched.append(argv)

        def poll(self):
            return None

    def run(argv, **kwargs):
        stdout = "trial-01" if argv[1] == "inspect" else json.dumps({
            "18765": False, "18766": True, "3128": True,
        })
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.subprocess, "Popen", Child)
    guardian.inspect_tenant("trial-01")
    guardian.inspect_tenant("trial-01")
    assert len(launched) == 1
    assert launched[0] == [
        "docker", "exec", "argus-web-trial-01", "python",
        "/bootstrap/socket_forward.py", "18765", "/meter/gateway.sock",
    ]


def test_rejects_other_container_ownership(guardian, monkeypatch):
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, stdout="other-owner"))
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        guardian.inspect_tenant("trial-01")


def test_probe_and_copied_relay_are_valid_python(guardian):
    compile(module.probe_script(), "<probe>", "exec")
    source = guardian.root / "tenants/trial-01/bootstrap/socket_forward.py"
    compile(source.read_text(), str(source), "exec")
    assert source.stat().st_mode & 0o077 == 0
