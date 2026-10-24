"""The PR gate lives in the argus-hosting submodule at ``hosting/`` (package ``argus_hosting``).

Argus must work without the submodule; when it is checked out, the entry points
Argus wires to it (the ``pr-gate`` workflow and ``scripts/pr-gate``) must run.
The gate's own tests live in the argus-hosting repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTING = ROOT / "hosting"
needs_hosting = pytest.mark.skipif(
    not (HOSTING / "argus_hosting" / "pr_gate" / "__main__.py").is_file(),
    reason="argus-hosting submodule not checked out (git submodule update --init hosting)",
)


def test_submodule_is_declared_at_hosting() -> None:
    text = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert '[submodule "hosting"]' in text
    assert "path = hosting" in text
    assert "url = https://github.com/lbx154/argus-hosting.git" in text


def test_core_never_imports_the_hosting_package() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "argus").rglob("*.py")
        if "argus_hosting" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_pr_gate_workflow_runs_the_gate_from_the_submodule() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pr-gate.yml").read_text(encoding="utf-8")
    assert "submodules: true" in workflow
    assert "PYTHONPATH: hosting" in workflow
    assert "python -m argus_hosting.pr_gate" in workflow


def test_launcher_explains_a_missing_submodule(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    launcher = shutil.copy2(ROOT / "scripts" / "pr-gate", tmp_path / "scripts" / "pr-gate")
    result = subprocess.run(
        [sys.executable, str(launcher), "--help"], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "git submodule update --init hosting" in result.stderr


@needs_hosting
def test_launcher_reaches_the_local_regression_gate() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pr-gate"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: pr-gate" in result.stdout


@needs_hosting
def test_description_gate_runs_from_the_submodule(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=repository, env=environment,
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q")
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repository / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    git("commit", "-q", "-am", "head")
    head = git("rev-parse", "HEAD")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {
        "title": "Raise the app value",
        "body": "Changes VALUE in app.py from one to two so callers see the new default.",
        "base": {"sha": base},
        "head": {"sha": head},
    }}), encoding="utf-8")
    output = tmp_path / "pr-gate-result.json"

    result = subprocess.run(
        [sys.executable, "-m", "argus_hosting.pr_gate",
         "--event", str(event), "--output", str(output)],
        cwd=repository, env={**environment, "PYTHONPATH": str(HOSTING)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "pr-gate/1.0"
    assert report["status"] == "passed"
