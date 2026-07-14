from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager.config_intent import _front_door_classify


class _Manager:
    def __init__(self, *, route: str) -> None:
        self.route = route

    def classify_front_door(
        self,
        text: str,
        *,
        lifetime_sink=None,
        fast_reply_sink=None,
        name_sink=None,
    ):
        if self.route == "complex" and lifetime_sink is not None:
            lifetime_sink("standing")
        if self.route == "simple" and fast_reply_sink is not None:
            fast_reply_sink("你好！我是 Argus。")
        if name_sink is not None:
            name_sink("test")
        return None, None, self.route


def test_front_door_wrapper_caches_team_lifetime_for_dispatch() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "keep improving",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(manager=_Manager(route="complex")),
    )

    assert decision == (None, None, "complex")
    assert state["_frontdoor_lifetime"] == "standing"
    assert "_frontdoor_fast_reply" not in state


def test_front_door_wrapper_caches_only_safe_social_reply() -> None:
    state: dict = {"_frontdoor_lifetime": "stale"}

    decision = _front_door_classify(
        object(),
        "你好",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(manager=_Manager(route="simple")),
    )

    assert decision == (None, None, "simple")
    assert state["_frontdoor_fast_reply"] == "你好！我是 Argus。"
    assert "_frontdoor_lifetime" not in state
