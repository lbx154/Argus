"""The merged cockpit front-door classifier (life.router.classify_front_door).

ONE model call decides BOTH axes — config-intent (SET.../NONE) and route
(SELF/TEAM → simple/complex) — replacing the two sequential classify calls the
cockpit used to make. These drive it with a fake ``run_exec`` and assert the
PARSING: both axes present, each axis falling to its OWN safe default in
isolation (a malformed CONFIG never corrupts ROUTE and vice-versa), and the
shared config-parse staying identical to classify_config_intent's.
"""
from __future__ import annotations

from argus_skill.life.router import (
    ConfigIntent,
    classify_config_intent,
    classify_front_door,
)


class _FakeResult:
    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg


def _exec(answer: str, exit_code: int = 0):
    def run_exec(prompt: str):
        assert "CONFIG:" in prompt and "ROUTE:" in prompt  # the merged prompt
        return _FakeResult(answer, exit_code)

    return run_exec


def test_both_axes_config_and_self() -> None:
    intent, route = classify_front_door(
        "用 copilot", run_exec=_exec("CONFIG: SET backend ALL copilot\nROUTE: SELF")
    )
    assert intent == ConfigIntent(knob="backend", roles=(), value="copilot")
    assert route == "simple"


def test_none_config_and_team() -> None:
    intent, route = classify_front_door(
        "优化 kernel", run_exec=_exec("CONFIG: NONE\nROUTE: TEAM")
    )
    assert intent is None
    assert route == "complex"


def test_role_scoped_config_with_route() -> None:
    intent, route = classify_front_door(
        "x", run_exec=_exec("CONFIG: SET effort engineer,reviewer high\nROUTE: SELF")
    )
    assert intent == ConfigIntent(knob="effort", roles=("engineer", "reviewer"), value="high")
    assert route == "simple"


def test_malformed_config_does_not_corrupt_route() -> None:
    # A garbled CONFIG line → None, but ROUTE still parses independently.
    intent, route = classify_front_door(
        "hi", run_exec=_exec("CONFIG: total garbage words\nROUTE: SELF")
    )
    assert intent is None
    assert route == "simple"


def test_missing_route_line_defaults_complex_config_still_parses() -> None:
    intent, route = classify_front_door(
        "x", run_exec=_exec("CONFIG: SET model engineer claude-sonnet-5")
    )
    assert intent == ConfigIntent(knob="model", roles=("engineer",), value="claude-sonnet-5")
    assert route == "complex"  # no ROUTE line → safe default


def test_unrecognized_route_token_is_complex() -> None:
    _, route = classify_front_door("x", run_exec=_exec("CONFIG: NONE\nROUTE: banana"))
    assert route == "complex"


def test_empty_text_no_model_call() -> None:
    called = [0]

    def _spy(prompt: str):
        called[0] += 1
        return _FakeResult("CONFIG: NONE\nROUTE: SELF")

    intent, route = classify_front_door("   ", run_exec=_spy)
    assert (intent, route) == (None, "complex")
    assert called[0] == 0  # never calls the model on empty input


def test_exec_error_is_safe_default() -> None:
    def _boom(prompt: str):
        raise RuntimeError("backend down")

    assert classify_front_door("y", run_exec=_boom) == (None, "complex")


def test_nonzero_exit_is_safe_default() -> None:
    intent, route = classify_front_door(
        "y", run_exec=_exec("CONFIG: SET backend ALL codex\nROUTE: SELF", exit_code=1)
    )
    assert (intent, route) == (None, "complex")


def test_config_parse_parity_with_classify_config_intent() -> None:
    # The shared _parse_config_line means the merged path and the standalone
    # classifier must produce the SAME ConfigIntent for the same SET line.
    line = "SET effort engineer,reviewer high"
    merged, _ = classify_front_door("x", run_exec=_exec(f"CONFIG: {line}\nROUTE: TEAM"))
    # standalone uses its own (non-merged) prompt, so a plain run_exec here.
    standalone = classify_config_intent("x", run_exec=lambda p: _FakeResult(line))
    assert merged == standalone == ConfigIntent("effort", ("engineer", "reviewer"), "high")


def test_prefixes_are_case_insensitive() -> None:
    intent, route = classify_front_door(
        "x", run_exec=_exec("config: SET safe_mode - on\nroute: self")
    )
    assert intent == ConfigIntent(knob="safe_mode", roles=(), value="on")
    assert route == "simple"
