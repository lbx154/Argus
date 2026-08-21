from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager.config_intent import _front_door_classify


class _Manager:
    def __init__(
        self,
        *,
        route: str,
        lifetime: str = "standing",
        self_mode: str = "inspect",
        reply: str = "",
        failure: str = "",
        control: str | None = None,
    ) -> None:
        self.route = route
        self.lifetime = lifetime
        self.self_mode = self_mode
        self.reply = reply
        self.failure = failure
        self.control = control

    def classify_front_door(
        self,
        text: str,
        *,
        lifetime_sink=None,
        self_mode_sink=None,
        reply_sink=None,
        greeting_sink=None,
        name_sink=None,
        failure_sink=None,
    ):
        if self.failure and failure_sink is not None:
            failure_sink(self.failure)
        if (
            self.route == "complex" or self.control == "steer"
        ) and lifetime_sink is not None:
            lifetime_sink(self.lifetime)
        if self.route == "simple" and self_mode_sink is not None:
            self_mode_sink(self.self_mode)
        if self.route == "simple" and reply_sink is not None and self.reply:
            reply_sink(self.reply)
        if self.route == "simple" and greeting_sink is not None:
            greeting_sink("你好，我是 Argus Manager。")
        if name_sink is not None:
            name_sink("test")
        return None, self.control, self.route


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


def test_front_door_wrapper_caches_explicit_bounded_increment() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "only complete research and stop",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(route="complex", lifetime="bounded_increment")
        ),
    )

    assert decision == (None, None, "complex")
    assert state["_frontdoor_lifetime"] == "bounded_increment"


def test_front_door_wrapper_caches_standing_lifetime_for_steer() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "keep working after this attempt",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(
                route="simple",
                lifetime="standing",
                control="steer",
            )
        ),
    )

    assert decision == (None, "steer", "simple")
    assert state["_frontdoor_lifetime"] == "standing"


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
    assert state["_frontdoor_self_mode"] == "inspect"
    assert state["_frontdoor_greeting_reply"] == "你好，我是 Argus Manager。"
    assert "_frontdoor_fast_reply" not in state


def test_existing_manager_thread_disables_context_free_fast_reply() -> None:
    state: dict = {"last_thread_id": "manager-thread-1"}

    decision = _front_door_classify(
        object(),
        "我选 B，先讲直觉",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(
                route="simple",
                self_mode="reply",
                reply="context-free answer",
            )
        ),
    )

    assert decision == (None, None, "simple")
    assert state["_frontdoor_self_mode"] == "inspect"
    assert "_frontdoor_fast_reply" not in state


def test_existing_manager_thread_preserves_isolated_execute_mode() -> None:
    state: dict = {"last_thread_id": "manager-thread-1"}

    decision = _front_door_classify(
        object(),
        "create one file and verify it",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(route="simple", self_mode="execute")
        ),
    )

    assert decision == (None, None, "simple")
    assert state["_frontdoor_self_mode"] == "execute"


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


def test_a_runner_build_failure_reports_why_it_failed() -> None:
    """"Please retry" is right for a backend hiccup and useless for broken
    project state, and only the reason tells the operator which one this is."""

    def _failing_builder(chat_state, _mem):
        chat_state["manager_runner_error"] = (
            "VerticalResolutionError: names vertical 'astrology'"
        )
        return None

    state: dict = {}

    decision = _front_door_classify(object(), "你好", state, ensure_runner=_failing_builder)

    assert decision == (None, None, "complex")
    assert state["_frontdoor_failure"] == (
        "classifier unavailable: VerticalResolutionError: names vertical 'astrology'"
    )


def test_a_build_failure_reason_does_not_outlive_a_later_success() -> None:
    """Reported per turn, not accumulated: the runner is cached across a whole
    cockpit session, so a stale reason would mislabel every later turn."""
    state: dict = {"manager_runner_error": "stale: last turn's backend timeout"}

    decision = _front_door_classify(
        object(),
        "summarize this paper",
        state,
        ensure_runner=lambda chat_state, _mem: (
            chat_state.pop("manager_runner_error", None),
            SimpleNamespace(manager=_Manager(route="complex")),
        )[1],
    )

    assert decision[2] == "complex"
    assert "_frontdoor_failure" not in state


def test_front_door_wrapper_marks_model_classification_failure() -> None:
    state: dict = {}

    decision = _front_door_classify(
        object(),
        "summarize this paper",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(
            manager=_Manager(
                route="complex",
                failure="classifier returned no valid route",
            )
        ),
    )

    assert decision == (None, None, "complex")
    assert state["_frontdoor_failure"] == "classifier returned no valid route"


def test_the_real_builder_records_the_exception_it_declines_to_raise(
    tmp_path, monkeypatch, caplog
) -> None:
    """The seam the reason has to come from. ``_ensure_manager_runner`` cannot
    raise — a build hiccup must not take down the cockpit turn — but returning
    a bare ``None`` discarded the one description of what went wrong, on a path
    where the operator is then told to retry.
    """
    import logging

    from argus_skill.apps import _runtime
    from argus_skill.manager.front_door import _ensure_manager_runner

    def _explode(_ns):
        raise RuntimeError("PIPELINE_STATE.json names vertical 'astrology'")

    monkeypatch.setattr(_runtime, "build_life_runner", _explode)
    mem = SimpleNamespace(
        project_root=tmp_path / "session",
        global_root=tmp_path / "global",
        root=tmp_path / "life",
    )
    state: dict = {"backend": "copilot"}

    with caplog.at_level(logging.ERROR, logger="argus_skill.manager.front_door"):
        assert _ensure_manager_runner(state, mem) is None

    assert "astrology" in state["manager_runner_error"]
    assert "RuntimeError" in state["manager_runner_error"]
    # Logged with its traceback too: the message above is one line, and the
    # frame that raised is what makes a permanent fault fixable.
    assert any(record.exc_info for record in caplog.records)
    # Not cached as unavailable — a transient failure must leave the next turn
    # free to build a working runner.
    assert "manager_runner" not in state
