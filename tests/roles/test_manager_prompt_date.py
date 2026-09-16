"""Manager reply prompts carry today's date.

Without it the model dates "the latest work" by its training cutoff: on the
2026-09-16 stable web trial a survey answer called 2024–2025 papers "最新".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argus.roles.prompts.manager import (
    build_quick_reply_prompt,
    build_simple_prompt,
    current_date_line,
)


def test_date_line_names_the_calendar_day_and_weekday() -> None:
    moment = datetime(2026, 9, 16, 0, 25, tzinfo=timezone(timedelta(hours=-7)))
    line = current_date_line(moment)
    assert "2026-09-16" in line
    assert "Wed" in line
    assert "training" in line.lower()


def test_reply_prompts_carry_todays_date() -> None:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    assert today in build_simple_prompt(objective="what is new in agentic RL?")
    assert today in build_quick_reply_prompt(objective="what is 2+2?")


def test_date_line_precedes_the_task_so_it_is_read_as_context() -> None:
    prompt = build_simple_prompt(objective="summarize the latest results")
    assert prompt.index(datetime.now().astimezone().strftime("%Y-%m-%d")) < prompt.index("Task:")
