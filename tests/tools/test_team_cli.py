from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.team import pool, registry
from argus_skill.tools import team


@pytest.fixture(autouse=True)
def _hermetic_project_root(tmp_path: Path, monkeypatch) -> None:
    """Keep campaign markers out of the operator's real project registry."""
    monkeypatch.setenv(
        "ARGUS_SKILL_PROJECT_ROOT",
        str(tmp_path / "_isolated_project_root"),
    )


def _call(capsys, *args: str) -> tuple[int, str]:
    rc = team.main(list(args))
    return rc, capsys.readouterr().out


def _tasks_file(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps(
            {
                "task_id": "a",
                "title": "A",
                "objective": "do A",
                "owns_paths": ["a/**"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_project_root_env_is_hermetic(tmp_path: Path) -> None:
    value = os.environ.get("ARGUS_SKILL_PROJECT_ROOT", "")
    assert value
    assert Path(value).is_relative_to(tmp_path)


def test_form_status_and_dissolve(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = _tasks_file(tmp_path)

    rc, _ = _call(
        capsys,
        "form",
        "--root",
        str(root),
        "--team-id",
        "t1",
        "--tasks",
        str(tasks),
    )
    assert rc == 0

    rc, out = _call(capsys, "status", "--root", str(root))
    status = json.loads(out)
    assert rc == 0
    assert status["roster"]["team_id"] == "t1"
    assert [task["task_id"] for task in status["tasks"]] == ["a"]
    assert status["members"] == []
    assert pool.read(root)["width"] == 0

    rc, _ = _call(capsys, "dissolve", "--root", str(root))
    assert rc == 0
    assert pool.read(root)["state"] == "dissolved"


def test_pool_set_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / "t"
    rc, out = _call(
        capsys,
        "pool-set",
        "--root",
        str(root),
        "--width",
        "6",
        "--state",
        "running",
    )
    doc = json.loads(out)
    assert rc == 0
    assert doc["width"] == 6 and doc["state"] == "running"
    assert "lead_heartbeat_ts" not in doc


def test_form_writes_campaign_marker(tmp_path: Path, capsys, monkeypatch) -> None:
    project_root = tmp_path / "proj"
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(project_root))
    root = tmp_path / ".argus_team" / "t1"
    tasks = _tasks_file(tmp_path)

    rc, _ = _call(
        capsys,
        "form",
        "--root",
        str(root),
        "--team-id",
        "t1",
        "--cwd",
        str(tmp_path / "ws"),
        "--tasks",
        str(tasks),
    )
    assert rc == 0
    markers = registry.list_markers(project_root)
    assert markers == [
        {
            "team_id": "t1",
            "team_root": str(root),
            "cwd": str(tmp_path / "ws"),
            "created_ts": markers[0]["created_ts"],
        }
    ]


def test_form_without_project_root_fails_before_writing_state(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_ROOT", raising=False)
    root = tmp_path / "t"
    rc, _ = _call(
        capsys,
        "form",
        "--root",
        str(root),
        "--team-id",
        "t1",
        "--tasks",
        str(_tasks_file(tmp_path)),
    )
    assert rc == 2
    assert not root.exists()
    assert registry.list_markers(tmp_path) == []
