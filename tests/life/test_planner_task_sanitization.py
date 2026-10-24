from __future__ import annotations

from argus.life.supervisor._helpers import _sanitize_planner_task_text


def test_sanitize_planner_task_text_does_not_replace_source_prefixes() -> None:
    text = (
        "Run PYTHONPATH=/home/legacy-user/Argus "
        "python -m argus, but preserve "
        "/home/legacy-user/Argus-backup/results.json."
    )

    sanitized = _sanitize_planner_task_text(text)

    assert "/home/legacy-user/Argus-backup/results.json" in sanitized
