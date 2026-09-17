"""Windows host initialization supplies the public plugin's private workspace parent."""
from __future__ import annotations

import os
import sys

import pytest

from argus.core import plugin_manager as pm
from argus.core import vertical_contract

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows plugin host compatibility")


@pytest.mark.parametrize("name", ["crystalpilot", "other_plugin"])
def test_workspace_parent_is_created_only_for_the_installed_crystal_plugin(tmp_path, monkeypatch, name):
    monkeypatch.setattr(pm, "_loaded", {})
    monkeypatch.setattr(vertical_contract, "vertical_contract", lambda *a: None)
    release = name + "/releases/fixture"
    directory = pm.install_root(tmp_path) / release
    package = directory / "package/fixture_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    # A minimal provider double: it intentionally does not create any directory.
    (package / "host.py").write_text(
        "from types import SimpleNamespace\n"
        "def create():\n"
        "    methods = ('configure_installation', 'mount', 'native_command', 'prepare_run', "
        "'finish_run', 'observe_stream', 'cancel_operations', 'accounting_root', "
        "'vertical_module', 'owns_workdir', 'is_busy', 'shutdown_workers')\n"
        "    plugin = SimpleNamespace(api_version=1, **{name: lambda *a, **k: None for name in methods})\n"
        f"    plugin.manifest = lambda: {{'id': {name!r}}}\n"
        "    return plugin\n",
        encoding="utf-8",
    )
    pm.write_json(directory / "plugin.json", {"factory": "fixture_plugin.host:create"})
    pm.write_json(pm.install_root(tmp_path) / "registry.json", {
        name: {"release": release, "python": sys.executable, "enabled": True},
    })
    workspace = tmp_path / "crystalpilot-runtime"
    assert not workspace.exists()
    plugin = pm.load_plugin(name, tmp_path)
    assert plugin is not None
    assert workspace.is_dir() is (name == "crystalpilot")
    if name == "crystalpilot":
        sentinel = workspace / "existing-research.txt"
        sentinel.write_text("keep this fixture unchanged", encoding="utf-8")
        assert pm.load_plugin(name, tmp_path) is plugin
        assert sentinel.read_text(encoding="utf-8") == "keep this fixture unchanged"
    assert not (tmp_path / "projects").exists()
