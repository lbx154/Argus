"""The front-door CONFIG line: one exact syntax, tolerant of near misses."""
from __future__ import annotations

import pytest

from argus.life.router import (
    ConfigIntent,
    _parse_config_decision,
    build_front_door_prompt,
    classify_front_door,
)


@pytest.mark.parametrize(
    "line",
    [
        "SET model ALL house-model-2",
        "SET role model=house-model-2 for ALL",
        "SET model=house-model-2",
        "SET model ALL=house-model-2",
        "SET model for ALL to house-model-2",
    ],
)
def test_config_variants_parse_to_the_same_intent(line: str) -> None:
    assert _parse_config_decision(line) == ConfigIntent(
        knob="model", roles=(), value="house-model-2"
    )


def test_role_scoped_variant_keeps_roles() -> None:
    expected = ConfigIntent(knob="effort", roles=("planner", "engineer"), value="high")
    assert _parse_config_decision("SET effort planner,engineer high") == expected
    assert _parse_config_decision("SET role effort=high for planner,engineer") == expected


@pytest.mark.parametrize(
    "line",
    [
        "SET mode=house-model-2",  # unknown knob
        "SET model ALL",  # no value
        "SET role model=whichever is best for ALL",  # vague value, never swallowed
    ],
)
def test_vague_config_lines_stay_unparsed(line: str) -> None:
    assert _parse_config_decision(line) is None


class _Result:
    exit_code = 0
    role_decisions: list = []
    fatal_error = None

    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg


def test_unrecognized_config_line_is_reported_not_dropped() -> None:
    failures: list[str] = []
    answer = "CONFIG: SET colour ALL blue\nROUTE: SELF\nSELF_MODE: INSPECT\n"
    intent, _control, _route = classify_front_door(
        "make it blue",
        run_exec=lambda _prompt: _Result(answer),
        config_failure_sink=failures.append,
    )
    assert intent is None
    assert failures == ["SET colour ALL blue"]


def test_front_door_prompt_spells_out_the_config_syntax() -> None:
    prompt = build_front_door_prompt("hi")
    assert "SET model ALL <id>" in prompt
    assert "SET effort planner,engineer high" in prompt
