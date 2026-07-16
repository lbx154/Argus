from __future__ import annotations

import json

import pytest

from scripts.generate_release_manifest import render
from scripts.set_binary_release_version import python_distribution_version


def test_commit_release_maps_to_valid_python_distribution_version() -> None:
    assert python_distribution_version("0.1.1-beta.g0123456789ab") == (
        "0.1.1b1250999896491"
    )
    with pytest.raises(ValueError):
        python_distribution_version("0.1.1-beta.3")


def test_release_manifest_uses_public_version_override(monkeypatch) -> None:
    public_version = "0.1.1-beta.g0123456789ab"
    monkeypatch.setenv("ARGUS_RELEASE_VERSION", public_version)
    manifest, generated = render()
    payload = json.loads(manifest)
    assert payload["package_version"] == public_version
    assert payload["release_id"].startswith(public_version + "+")
    assert public_version in generated
