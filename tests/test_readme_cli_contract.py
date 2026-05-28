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
    """The codex backend is now bundled (codex_autoloop vendored).

    README must point users at the bundled installation path and away
    from the old ArgusBot pip-install step, and the built wheel must
    *not* declare an ArgusBot dependency under the ``codex`` extra.
    Keeping the empty ``codex`` extra so legacy
    ``pip install 'argus-skill[codex]'`` invocations are still no-ops
    instead of hard errors is fine.
    """
    repo = _repo_root()
    readme = (repo / "README.md").read_text(encoding="utf-8")
    # README must NOT push the legacy "install ArgusBot via the extra" lane.
    assert "pip install 'argus-skill[codex]'" not in readme, (
        "README should drop the legacy '[codex]' install step now that "
        "codex_autoloop is vendored under argus_skill/."
    )
    assert "pip install -e ../ArgusBot" not in readme
    # README must explain how to install the codex CLI binary itself.
    assert "@openai/codex" in readme, (
        "README should mention `npm install -g @openai/codex` so users "
        "know how to install the codex CLI prerequisite."
    )

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
    # Empty extra is permitted for back-compat, but ArgusBot must NOT
    # be a runtime dependency of the wheel any more. Only inspect
    # dependency-declaration headers; the README itself ships inside
    # the metadata as the long description and is allowed to mention
    # ArgusBot in narrative text.
    dep_lines = [
        line for line in metadata
        if line.startswith(("Requires-Dist:", "Provides-Extra:"))
    ]
    for line in dep_lines:
        assert "argusbot" not in line.lower(), (
            f"wheel metadata still declares ArgusBot dependency: {line!r}; "
            "the module is vendored, the dependency must be removed."
        )

    # Wheel must actually ship the vendored package.
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert any(
        n.startswith("argus_skill/codex_autoloop/") and n.endswith(".py")
        for n in names
    ), "wheel does not contain the vendored argus_skill/codex_autoloop/ Python files"


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
