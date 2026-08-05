from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager.config_intent import _front_door_classify


class _Manager:
    def __init__(self, *, route: str, lifetime: str = "standing") -> None:
        self.route = route
        self.lifetime = lifetime

    def classify_front_door(
        self,
        text: str,
        *,
        lifetime_sink=None,
        greeting_sink=None,
        name_sink=None,
    ):
        if self.route == "complex" and lifetime_sink is not None:
            lifetime_sink(self.lifetime)
        if self.route == "simple" and greeting_sink is not None:
            greeting_sink("你好，我是 Argus Manager。")
        if name_sink is not None:
            name_sink("test")
        return None, None, self.route


def test_front_door_wrapper_routes_team_work_as_complex() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "keep improving",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(manager=_Manager(route="complex")),
    )

    assert decision == (None, None, "complex")
    # Classification does not pre-commit a vertical; the Manager decides later.
    assert "_frontdoor_vertical" not in state
    assert state["_frontdoor_lifetime"] == "standing"


def test_front_door_wrapper_caches_bounded_lifetime_for_dispatch() -> None:
    """A TEAM request keeps the same-call bounded verdict for dispatch."""
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "write one report",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(route="complex", lifetime="bounded")
        ),
    )

    assert decision == (None, None, "complex")
    assert state["_frontdoor_lifetime"] == "bounded"


def test_front_door_wrapper_carries_one_turn_greeting_reply() -> None:
    state: dict = {"_frontdoor_lifetime": "stale"}

    decision = _front_door_classify(
        object(),
        "你好",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(manager=_Manager(route="simple")),
    )

    assert decision == (None, None, "simple")
    assert "_frontdoor_lifetime" not in state
    assert state["_frontdoor_greeting_reply"] == "你好，我是 Argus Manager。"
    assert "_frontdoor_fast_reply" not in state


def test_front_door_wrapper_marks_classifier_unavailable() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "你好",
        state,
        ensure_runner=lambda *_args: None,
    )

    assert decision == (None, None, "complex")
    assert state["_frontdoor_failure"] == "classifier unavailable"
