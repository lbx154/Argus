"""Contract tests for the documented CLI surface in README.md."""
from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _wheel_metadata_lines(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        metadata_name = next(name for name in zf.namelist() if name.endswith("METADATA"))
        return zf.read(metadata_name).decode("utf-8").splitlines()


def test_readme_mentions_public_one_shot_flags() -> None:
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")

    expected_fragments = [
        "--follow",
        "--notify MSG",
        "--init-identity",
        "--skill-stats-json",
        "--skill-cleanse",
        "--skill-compact",
        "--apply",
        "--daemon-runbook",
        "pytest -q",
    ]
    missing = [fragment for fragment in expected_fragments if fragment not in readme]
    assert not missing, f"README missing fragments: {missing}"


def test_readme_matches_codex_extra_contract(tmp_path: Path) -> None:
    repo = _repo_root()
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "pip install 'argus-skill[codex]'" in readme
    assert "pip install -e ../ArgusBot" not in readme

    outdir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    wheel = next(outdir.glob("*.whl"))
    metadata = _wheel_metadata_lines(wheel)
    assert "Provides-Extra: codex" in metadata
    assert (
        "Requires-Dist: argusbot @ git+https://github.com/waltstephen/ArgusBot.git ; "
        "extra == 'codex'"
    ) in metadata


def test_pyproject_dev_extra_includes_build() -> None:
    pyproject = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert any(
        entry.partition(";")[0].strip().startswith("build")
        for entry in project["optional-dependencies"]["dev"]
    )
    assert "build" not in project["dependencies"]


def test_readme_drops_stale_quality_gate_claims() -> None:
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")
    for stale_phrase in (
        "runs in <1s",
        "uses only the in-memory backend",
    ):
        assert stale_phrase not in readme
