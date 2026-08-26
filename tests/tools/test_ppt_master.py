from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus_skill.tools import ppt_master as ppt_master_module
from argus_skill.tools.ppt_master import (
    install_ppt_master,
    install_root,
    ppt_master_status,
    skill_root,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fake_upstream(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "upstream"
    skill = repo / "skills" / "ppt-master"
    (skill / "workflows").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (repo / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2025-2026 Hugo He\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("# PPT Master\n", encoding="utf-8")
    (skill / "workflows" / "routing.md").write_text("# Routing\n", encoding="utf-8")
    (skill / "workflows" / "generate-pptx.md").write_text(
        "# Generate\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "project_manager.py").write_text("", encoding="utf-8")
    (skill / "scripts" / "svg_to_pptx.py").write_text("", encoding="utf-8")
    (skill / "requirements.txt").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_install_ppt_master_clones_and_validates_pinned_toolkit(tmp_path: Path) -> None:
    upstream, revision = _fake_upstream(tmp_path)
    home = tmp_path / "argus-home"

    status = install_ppt_master(
        global_root=home,
        repository=str(upstream),
        revision=revision,
        install_dependencies=False,
    )

    assert status.installed is True
    assert status.valid is True
    assert status.revision == revision
    assert status.dependencies_installed is False
    assert install_root(home).is_dir()
    assert (skill_root(home) / "SKILL.md").is_file()


def test_status_reports_missing_install(tmp_path: Path) -> None:
    status = ppt_master_status(global_root=tmp_path)

    assert status.installed is False
    assert status.valid is False
    assert status.detail == "not installed"


def test_install_refuses_modified_existing_checkout(tmp_path: Path) -> None:
    upstream, revision = _fake_upstream(tmp_path)
    home = tmp_path / "argus-home"
    install_ppt_master(
        global_root=home,
        repository=str(upstream),
        revision=revision,
        install_dependencies=False,
    )
    (skill_root(home) / "SKILL.md").write_text("modified\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="modified PPT Master checkout"):
        install_ppt_master(
            global_root=home,
            repository=str(upstream),
            revision=revision,
            install_dependencies=False,
        )


def test_failed_dependency_install_keeps_previous_revision(tmp_path: Path) -> None:
    upstream, first_revision = _fake_upstream(tmp_path)
    home = tmp_path / "argus-home"
    install_ppt_master(
        global_root=home,
        repository=str(upstream),
        revision=first_revision,
        install_dependencies=False,
    )
    (upstream / "skills" / "ppt-master" / "SKILL.md").write_text(
        "# PPT Master v2\n",
        encoding="utf-8",
    )
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-m", "v2")
    second_revision = _git(upstream, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="PPT Master command failed"):
        install_ppt_master(
            global_root=home,
            repository=str(upstream),
            revision=second_revision,
            python_executable="/bin/false",
            install_dependencies=True,
        )

    assert _git(install_root(home), "rev-parse", "HEAD") == first_revision


def test_status_rejects_dirty_checkout(tmp_path: Path) -> None:
    upstream, revision = _fake_upstream(tmp_path)
    home = tmp_path / "argus-home"
    install_ppt_master(
        global_root=home,
        repository=str(upstream),
        revision=revision,
        install_dependencies=False,
    )
    (skill_root(home) / "SKILL.md").write_text("modified\n", encoding="utf-8")

    status = ppt_master_status(global_root=home, expected_revision=revision)

    assert status.valid is False
    assert status.dependencies_installed is False
    assert status.detail == "tracked toolkit files are modified"


def test_status_requires_dependencies_for_current_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, revision = _fake_upstream(tmp_path)
    home = tmp_path / "argus-home"
    install_ppt_master(
        global_root=home,
        repository=str(upstream),
        revision=revision,
        install_dependencies=True,
    )
    monkeypatch.setenv("ARGUS_SKILL_PYTHON", "/bin/false")

    status = ppt_master_status(global_root=home, expected_revision=revision)

    assert status.valid is True
    assert status.dependencies_installed is False
    assert status.detail == "toolkit installed; dependencies not recorded for this Python"


def test_dependency_install_uses_uv_when_python_has_no_pip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(ppt_master_module, "_python_has_pip", lambda _python: False)
    monkeypatch.setattr(
        ppt_master_module.shutil,
        "which",
        lambda command: "/usr/local/bin/uv" if command == "uv" else None,
    )
    monkeypatch.setattr(
        ppt_master_module,
        "_run",
        lambda argv, **_kwargs: calls.append(list(argv)),
    )

    ppt_master_module._install_requirements("/managed/python", requirements)

    assert calls == [[
        "/usr/local/bin/uv",
        "pip",
        "install",
        "--python",
        "/managed/python",
        "-r",
        str(requirements),
    ]]
