import io
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from argus.release_tools import provision_platon as tool


@pytest.fixture
def offline(tmp_path, monkeypatch):
    root = tmp_path / "tenant"
    resources = root / "extensions/crystalpilot/resources"
    directory = resources / "software/platon-reviewed"
    (directory / "lib").mkdir(parents=True)
    (directory / "platon").write_bytes(b"reviewed executable")
    trusted = tmp_path / "trusted-host/software/platon-reviewed"
    trusted.mkdir(parents=True)
    (trusted / "platon").write_bytes(b"reviewed executable")
    record = {
        "path": str(trusted / "platon"),
        "libs": [str(trusted / "lib")],
        "environment": {"CRYSTALPILOT_PLATON": str(trusted / "platon")},
        "version": "2026-06-19",
        "recipe": "native-2026-06",
    }
    plugin = Mock()
    monkeypatch.setattr(tool.manager, "_busy", Mock())
    monkeypatch.setattr(tool.manager, "load_plugin", lambda *a: plugin)
    monkeypatch.setattr(tool.manager, "state_entry", lambda *a: {"python": "/science/bin/python"})
    run = Mock(return_value=json.dumps({"components": [{"id": "platon", "status": "ready"}]}))
    monkeypatch.setattr(tool.runtime, "run", run)
    return root, directory, record, plugin, run


@pytest.mark.parametrize("mutation", ["different_source", "outside_library", "escape_symlink", "outside_directory"])
def test_rejects_untrusted_or_cross_tenant_inputs(offline, mutation, tmp_path):
    root, directory, record, plugin, run = offline
    if mutation == "different_source":
        Path(record["path"]).write_bytes(b"not the reviewed executable")
    elif mutation == "outside_library":
        record["libs"] = ["/another-tenant/lib"]
    elif mutation == "escape_symlink":
        (directory / "escape").symlink_to(tmp_path)
    else:
        directory = tmp_path
    with pytest.raises(ValueError):
        tool.provision(root, directory, record)
    run.assert_not_called()
    plugin.shutdown_workers.assert_not_called()


def test_active_installer_is_not_interrupted(offline, monkeypatch):
    root, directory, record, plugin, run = offline
    monkeypatch.setattr(tool.manager, "read_json", lambda *a: {"status": "running"})
    monkeypatch.setattr(tool.manager, "_job_alive", lambda *a: True)
    with pytest.raises(tool.manager.PluginError, match="active environment"):
        tool.provision(root, directory, record)
    run.assert_not_called()
    plugin.shutdown_workers.assert_not_called()


def test_failed_plugin_probe_is_not_reported_as_success(offline):
    root, directory, record, plugin, run = offline
    run.side_effect = RuntimeError("PLATON did not generate check.def")
    with pytest.raises(RuntimeError, match="check.def"):
        tool.provision(root, directory, record)
    operation = tool.manager.read_json(root / "extensions/crystalpilot/operation.json")
    assert operation["status"] == "failed"
    assert "check.def" in operation["error"]


def test_cli_reads_record_and_returns_health(offline, capsys):
    root, directory, record, plugin, run = offline
    path = directory / "source-record.json"
    path.write_text(json.dumps(record))
    assert tool.main(["--root", str(root), "--directory", str(directory), "--record", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["components"][0]["status"] == "ready"


@pytest.mark.parametrize("status", ["ready", "broken"])
def test_science_activation_requires_real_platon_health(monkeypatch, capsys, status):
    dependencies = ModuleType("argus_crystalpilot.dependencies")
    dependencies._register = Mock()
    dependencies.health = Mock(return_value={
        "components": [{"id": "platon", "status": status, "detail": "probe result"}],
    })
    package = ModuleType("argus_crystalpilot")
    package.dependencies = dependencies
    monkeypatch.setitem(sys.modules, "argus_crystalpilot", package)
    payload = {"root": "/tenant/resources", "record": {"libs": ["/tenant/resources/software/platon/lib"]}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    if status == "ready":
        exec(tool.ACTIVATE, {})
        assert json.loads(capsys.readouterr().out)["components"][0]["status"] == "ready"
    else:
        with pytest.raises(RuntimeError, match="probe result"):
            exec(tool.ACTIVATE, {})
        assert capsys.readouterr().out == ""
    dependencies._register.assert_called_once_with(payload["root"], "platon", payload["record"])
