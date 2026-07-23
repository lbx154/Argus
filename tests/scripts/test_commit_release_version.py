from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.generate_release_manifest import render
from scripts import set_binary_release_version
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


def test_binary_version_prepares_manifest_for_frontend_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "argus_skill").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.1"\n',
        encoding="utf-8",
    )
    (tmp_path / "argus_skill" / "__init__.py").write_text(
        '__version__ = "0.1.1"\n',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def fake_run(
        argv: list[str],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> SimpleNamespace:
        assert check is True
        calls.append((argv, cwd, env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(set_binary_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(set_binary_release_version.subprocess, "run", fake_run)

    version = "0.1.1-beta.g0123456789ab"
    assert set_binary_release_version.main([version]) == 0
    assert len(calls) == 1
    argv, cwd, env = calls[0]
    assert argv == [
        set_binary_release_version.sys.executable,
        "scripts/generate_release_manifest.py",
        "--prepare-build",
    ]
    assert cwd == tmp_path
    assert env["ARGUS_RELEASE_VERSION"] == version
