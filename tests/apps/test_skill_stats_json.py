"""Regression tests for ``argus-skill --skill-stats-json``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.apps.cli import main


def test_skill_stats_json_main_emits_json_and_skips_tui(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    life_dir = tmp_path / "life"
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(
        "argus_skill.apps.tui_launcher.main",
        lambda *args, **kwargs: pytest.fail(
            "TUI must not be entered for --skill-stats-json"
        ),
    )

    monkeypatch.chdir(repo)
    rc = main(["--skill-stats-json", "--life-dir", str(life_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    assert data["totals"] == {
        "missions": 0,
        "hits": 0,
        "distills": 0,
        "cold": 0,
        "successes": 0,
        "hit_rate": 0.0,
        "distill_rate": 0.0,
    }
    assert data["by_bucket"]["hit"]["missions"] == 0
    assert data["by_skill"] == {}
    assert "argus ›" not in out
    assert "skill effectiveness report" not in out
