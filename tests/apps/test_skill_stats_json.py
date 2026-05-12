"""Regression tests for ``argus-skill --skill-stats-json``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.apps.cli import main


def test_skill_stats_json_main_emits_json_and_skips_repl(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    life_dir = tmp_path / "life"
    life_dir.mkdir()
    (life_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "skill.outcome",
                        "skill_name": "github.com/foo/bar",
                        "skill_hit": True,
                        "skill_distilled": False,
                        "success": True,
                        "rounds": 2,
                        "matcher_tokens": 17,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "argus_skill.apps._life_repl.run_life_chat_loop",
        lambda *args, **kwargs: pytest.fail(
            "REPL must not be entered for --skill-stats-json"
        ),
    )

    rc = main(["--skill-stats-json", "--life-dir", str(life_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    assert data["totals"]["missions"] == 1
    assert data["totals"]["hits"] == 1
    assert data["by_bucket"]["hit"]["missions"] == 1
    assert "argus ›" not in out
    assert "skill effectiveness report" not in out
