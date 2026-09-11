"""A public host build must retain separately published plugin artifacts."""

import json

import pytest

from argus_skill.release_tools import build_plugins


def test_build_preserves_external_catalog_without_private_source(tmp_path, monkeypatch):
    monkeypatch.setattr(build_plugins, "ROOT", tmp_path)
    directory = tmp_path / "argus_skill"
    directory.mkdir()
    entry = {
        "id": "external",
        "version": "1.2.3",
        "artifact": {
            "filename": "external-1.2.3-py3-none-any.whl",
            "sha256": "a" * 64,
            "url": "https://downloads.example.org/external.whl",
            "local": "/maintainer/only.whl",
        },
    }
    path = directory / "plugin_catalog.json"
    path.write_text(json.dumps({"plugins": [entry]}))
    monkeypatch.setattr(
        build_plugins.subprocess,
        "run",
        lambda *a, **k: pytest.fail("External plugin must not be built or installed"),
    )
    build_plugins.main()
    published = json.loads(path.read_text())
    assert published["plugins"][0]["artifact"]["sha256"] == "a" * 64
    assert "local" not in published["plugins"][0]["artifact"]
    assert json.loads((tmp_path / "dist-plugins/catalog.json").read_text()) == published
    build_plugins.main()
    assert json.loads(path.read_text()) == published


def test_invalid_external_catalog_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(build_plugins, "ROOT", tmp_path)
    directory = tmp_path / "argus_skill"
    directory.mkdir()
    (directory / "plugin_catalog.json").write_text(
        json.dumps({"plugins": [{"id": "bad", "artifact": {"url": "http://unsafe.example"}}]})
    )
    with pytest.raises(ValueError, match="HTTPS release URL and SHA-256"):
        build_plugins.main()
