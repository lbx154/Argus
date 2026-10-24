"""Keep pinned science constraints separate from the immutable wheel identity."""
from __future__ import annotations

import pytest

from argus.core import plugin_manager as pm


def test_catalog_keeps_original_wheel_and_adds_scientific_compatibility():
    spec = pm.catalog()["crystalpilot"]
    assert spec["version"] == "0.4.0"
    assert spec["artifact"]["filename"] == "argus_crystalpilot-0.4.0-py3-none-any.whl"
    assert spec["python_constraints"] == ["numpy<2"]
    assert spec["validation_imports"] == ["numpy", "crystalpilot.refine.project"]
    row = {"version": spec["version"], "git_commit": spec.get("git_commit"), "sha256": "ignored digest"}
    assert not pm.matches_catalog(row, spec, None)
    row.update(python_constraints=spec["python_constraints"], validation_imports=spec["validation_imports"])
    assert pm.matches_catalog(row, spec, None)


@pytest.mark.parametrize("fields", [
    {"python_constraints": ["numpy<2\n--trusted-host example.invalid"]},
    {"python_constraints": "numpy<2"},
    {"validation_imports": ["os;print('not a module')"]},
])
def test_catalog_cannot_inject_installer_options_or_python_source(tmp_path, fields):
    with pytest.raises(pm.PluginError):
        pm._python_install_environment(fields, tmp_path)
