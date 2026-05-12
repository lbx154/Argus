from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import venv
import zipfile
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_cli(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "argus-skill.exe"
    return venv_dir / "bin" / "argus-skill"


def _artifact_contains(wheel: Path, member_suffix: str) -> bool:
    with zipfile.ZipFile(wheel) as zf:
        return any(name.endswith(member_suffix) for name in zf.namelist())


def _sdist_contains(sdist: Path, member_suffix: str) -> bool:
    with tarfile.open(sdist, "r:gz") as tf:
        return any(member.name.endswith(member_suffix) for member in tf.getmembers())


def test_built_artifacts_and_installed_cli_contract(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    sdist = next(outdir.glob("*.tar.gz"))
    assert _artifact_contains(wheel, "argus_skill/skills/__init__.py")
    assert _artifact_contains(wheel, "argus_skill/skills/store.py")
    assert _sdist_contains(sdist, "argus_skill/skills/__init__.py")
    assert _sdist_contains(sdist, "argus_skill/skills/store.py")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=repo)

    cli = _venv_cli(venv_dir)
    help_run = _run([str(cli), "--help"], cwd=repo)
    assert help_run.returncode == 0

    import_run = _run(
        [str(venv_python), "-c", "import argus_skill.skills"],
        cwd=repo,
    )
    assert import_run.returncode == 0

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    status_run = _run([str(cli), "--status", "--life-dir", str(life_dir)], cwd=repo)
    assert status_run.returncode == 0

    watch_life_dir = tmp_path / "watch-life"
    watch_life_dir.mkdir()
    watch_fallback = subprocess.run(
        [str(cli), "--watch", "--life-dir", str(watch_life_dir)],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert watch_fallback.returncode == 2
    assert "watch: rich is required for the live cockpit" in watch_fallback.stderr

    missing_dir = tmp_path / "missing-life"
    watch_run = subprocess.run(
        [str(cli), "--watch", "--life-dir", str(missing_dir)],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert watch_run.returncode == 2
    assert f"watch: life-dir not found: {missing_dir}" in watch_run.stderr
    assert "Traceback" not in watch_run.stderr
