"""Preview rebuilds keep a committed artifact identity but detect source changes."""
from __future__ import annotations

import json
import subprocess

import pytest

from argus.release_tools import generate_manifest as generator


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_RELEASE_VERSION", raising=False)
    root = tmp_path / "source"
    (root / "argus").mkdir(parents=True)
    (root / "frontend/core/src").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.10"\n', encoding="utf-8"
    )
    (root / "argus/module.py").write_text('VALUE = "one"\n', encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "MANIFEST_PATH", root / "argus/release_manifest.json")
    monkeypatch.setattr(generator, "TS_PATH", root / "frontend/core/src/release.generated.ts")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "user.name=Preview test",
             "-c", "user.email=preview-test@example.invalid", *args],
            stderr=subprocess.STDOUT, text=True,
        ).strip()

    git("init")
    git("config", "core.autocrlf", "false")
    git("add", "--", "pyproject.toml", "argus/module.py")
    git("commit", "-m", "source")
    source_commit = git("rev-parse", "HEAD")
    generator.main(["--prepare-build"])
    git("add", "--", "argus/release_manifest.json", "frontend/core/src/release.generated.ts")
    git("commit", "-m", "generated artifacts")
    return root, git, source_commit


def identity():
    return json.loads(generator.MANIFEST_PATH.read_text(encoding="utf-8"))


def test_rebuild_after_generated_commit_is_byte_identical(checkout):
    _, git, source = checkout
    original = generator.MANIFEST_PATH.read_bytes(), generator.TS_PATH.read_bytes()
    assert git("rev-parse", "HEAD") != source
    assert generator.main(["--prepare-build"]) == 0
    assert (generator.MANIFEST_PATH.read_bytes(), generator.TS_PATH.read_bytes()) == original
    assert identity()["git_commit"] == source
    assert git("status", "--porcelain") == ""
    assert generator.main(["--check"]) == 0


def test_same_length_committed_source_edit_starts_new_identity(checkout):
    root, git, source = checkout
    path = root / "argus/module.py"
    original_size = path.stat().st_size
    path.write_text('VALUE = "two"\n', encoding="utf-8")
    assert path.stat().st_size == original_size
    git("add", "--", "argus/module.py")
    git("commit", "-m", "same-length source edit")
    generator.main(["--prepare-build"])
    assert identity()["git_commit"] == git("rev-parse", "HEAD") != source


def test_same_length_uncommitted_source_edit_does_not_reuse_old_identity(checkout):
    root, git, source = checkout
    (root / "argus/module.py").write_text('VALUE = "two"\n', encoding="utf-8")
    generator.main(["--prepare-build"])
    assert identity()["git_commit"] == git("rev-parse", "HEAD") != source


def test_new_source_input_does_not_reuse_old_identity(checkout):
    root, git, source = checkout
    (root / "argus/new_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    generator.main(["--prepare-build"])
    assert identity()["git_commit"] == git("rev-parse", "HEAD") != source
    assert "argus/new_module.py" in identity()["source_files"]


def test_changed_package_version_does_not_reuse_old_identity(checkout):
    root, git, source = checkout
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.11"\n', encoding="utf-8"
    )
    generator.main(["--prepare-build"])
    assert identity()["package_version"] == "0.1.11"
    assert identity()["git_commit"] == git("rev-parse", "HEAD") != source
