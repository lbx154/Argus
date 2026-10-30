import json

from argus.release_tools import generate_manifest


def test_python_manifest_generation_has_no_frontend_output(tmp_path, monkeypatch):
    (tmp_path / "argus").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    manifest = tmp_path / "argus" / "release_manifest.json"
    monkeypatch.setattr(generate_manifest, "ROOT", tmp_path)
    monkeypatch.setattr(generate_manifest, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(generate_manifest, "_head_commit", lambda: "0123456789abcdef")
    monkeypatch.delenv("ARGUS_RELEASE_VERSION", raising=False)

    assert generate_manifest.main(["--prepare-build"]) == 0
    assert json.loads(manifest.read_text())["git_commit"] == "0123456789abcdef"
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "argus/release_manifest.json", "pyproject.toml",
    ]
    assert generate_manifest.main(["--check"]) == 0
