"""Manager reply prompts carry today's date.

Without it the model dates "the latest work" by its training cutoff: on the
2026-09-16 stable web trial a survey answer called 2024–2025 papers "最新".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argus.roles.prompts.manager import (
    build_manager_prompt,
    current_date_line,
)


def test_date_line_names_the_calendar_day_and_weekday() -> None:
    moment = datetime(2026, 9, 16, 0, 25, tzinfo=timezone(timedelta(hours=-7)))
    line = current_date_line(moment)
    assert "2026-09-16" in line
    assert "Wed" in line
    assert "training" in line.lower()




def test_the_manager_prompt_carries_todays_date(tmp_path) -> None:
    prompt = build_manager_prompt("what is new in agentic RL?", workspace=tmp_path, state_root=tmp_path)
    assert current_date_line() in prompt
