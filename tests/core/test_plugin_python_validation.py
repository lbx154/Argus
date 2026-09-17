"""Keep pinned science constraints separate from the immutable wheel identity."""
from __future__ import annotations

import copy
import os
import subprocess
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest

from argus.core import plugin_manager as pm


def test_catalog_keeps_original_wheel_and_adds_scientific_compatibility():
    spec = pm.catalog()["crystalpilot"]
    assert spec["version"] == "0.4.0"
    assert spec["artifact"]["sha256"] == "030f22e7c18cb411db43f42fc4667cba06da1593a8a2054cc1a8e7e1fd12fed2"
    assert spec["python_constraints"] == ["numpy<2"]
    assert spec["validation_imports"] == ["numpy", "crystalpilot.refine.project"]
    row = {"version": spec["version"], "sha256": spec["artifact"]["sha256"]}
    assert not pm.matches_catalog(row, spec, None)
    row.update(python_constraints=spec["python_constraints"], validation_imports=spec["validation_imports"])
    assert pm.matches_catalog(row, spec, None)


def test_nested_pip_constraint_uses_a_local_uri_with_no_whitespace(tmp_path, monkeypatch):
    destination = tmp_path / "中文 scientific environment"
    destination.mkdir()
    monkeypatch.setenv("PIP_CONSTRAINT", "existing-constraint.txt")
    env = pm._python_install_environment(pm.catalog()["crystalpilot"], destination)
    existing, own = env["PIP_CONSTRAINT"].split()
    assert existing == "existing-constraint.txt"
    assert urlparse(own).scheme == "file"
    assert "%20" in own
    assert "中文 scientific environment" in unquote(own)
    assert (destination / "python-constraints.txt").read_text(encoding="utf-8") == "numpy<2\n"
    assert os.environ["PIP_CONSTRAINT"] == "existing-constraint.txt"


@pytest.mark.parametrize("fields", [
    {"python_constraints": ["numpy<2\n--trusted-host example.invalid"]},
    {"python_constraints": "numpy<2"},
    {"validation_imports": ["os;print('not a module')"]},
])
def test_catalog_cannot_inject_installer_options_or_python_source(tmp_path, fields):
    with pytest.raises(pm.PluginError):
        pm._python_install_environment(fields, tmp_path)


@pytest.mark.parametrize("failure", [None, "pip-check", "import-check"])
def test_candidate_is_activated_only_after_science_checks(tmp_path, monkeypatch, failure):
    root = tmp_path / "host"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(root))
    spec = copy.deepcopy(pm.catalog()["crystalpilot"])
    spec["setup"] = {}
    old = {"release": "crystalpilot/releases/old", "enabled": True, "version": "0.4.0"}
    pm.write_json(pm.install_root(root) / "registry.json", {"crystalpilot": old})
    monkeypatch.setattr(pm, "_python", lambda *_: "fixture-python")
    monkeypatch.setattr(pm, "_fetch", lambda *a: None)
    monkeypatch.setattr(pm, "_extract", lambda *a: None)
    monkeypatch.setattr(pm, "_busy", lambda *a: None)
    calls = []
    activated = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if failure == "pip-check" and args[-2:] == ["pip", "check"]:
            raise subprocess.CalledProcessError(1, args)
        if failure == "import-check" and "faulthandler" in args:
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(returncode=0)

    def load(*args, **kwargs):
        if kwargs.get("_candidate"):
            activated.append(kwargs["_candidate"])
        return None

    monkeypatch.setattr(pm.subprocess, "run", run)
    monkeypatch.setattr(pm, "load_plugin", load)
    pm._install("crystalpilot", spec, root)
    row = pm.registry(root)["crystalpilot"]
    operation = pm.read_json(pm.install_root(root) / "crystalpilot" / "operation.json")
    if failure:
        assert operation["status"] == "failed"
        assert row == old and not activated
    else:
        assert operation["status"] == "completed" and activated
        assert row["python_constraints"] == ["numpy<2"]
        assert row["validation_imports"] == spec["validation_imports"]
        assert any(args[-2:] == ["pip", "check"] for args, _ in calls)
        assert any("faulthandler" in args for args, _ in calls)
        installs = [(args, kw) for args, kw in calls if "--prefix" in args or "install" in args]
        assert len(installs) == 2
        assert all("PIP_CONSTRAINT" in kw["env"] for _, kw in installs)
