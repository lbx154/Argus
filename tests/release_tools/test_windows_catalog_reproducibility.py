"""Windows source generation must preserve Git's LF-only release identity."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.release import compute_source_digest
from argus_skill.release_tools import build_plugins


def test_plugin_catalog_uses_canonical_lf_and_preserves_external_release(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "argus_skill/verticals").mkdir(parents=True)
    entry = {
        "id": "external-fixture", "package": "external_fixture", "version": "0.4.0",
        "license": "proprietary", "host_api": 1,
        "artifact": {"filename": "external_fixture-0.4.0-py3-none-any.whl",
                     "sha256": "a" * 64, "url": "https://example.invalid/external_fixture.whl"},
    }
    source = tmp_path / "argus_skill/plugin_catalog.json"
    source.write_bytes(json.dumps({"plugins": [entry]}, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    def forbid_plugin_build(*args, **kwargs):
        raise AssertionError("external plugins must not be rebuilt")

    with monkeypatch.context() as scoped:
        scoped.setattr(build_plugins, "ROOT", tmp_path)
        scoped.setattr(build_plugins.subprocess, "run", forbid_plugin_build)
        build_plugins.main()

    data = source.read_bytes()
    assert b"\r\n" not in data
    assert data.endswith(b"\n")
    assert json.loads(data) == {"plugins": [entry]}
    assert b"\r\n" not in (tmp_path / "dist-plugins/catalog.json").read_bytes()
    digest = compute_source_digest(tmp_path)
    # A Git checkout obeying .gitattributes must have exactly the same identity.
    source.write_bytes(data.replace(b"\r\n", b"\n"))
    assert compute_source_digest(tmp_path) == digest
