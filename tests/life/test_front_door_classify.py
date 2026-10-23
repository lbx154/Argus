"""The merged cockpit front-door classifier (life.router.classify_front_door).

ONE model call fills the ``front_door`` form: config intent, operator control
and the manager/team route. Only the form it handed off is read.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.handoff import form_tool
from argus.life.router import ConfigIntent, classify_front_door
from argus.roles.prompts.manager import build_front_door_prompt
from argus.roles.prompts.voice import RESEARCHER_VOICE, RESEARCHER_VOICE_BRIEF


def _form(**fields: Any) -> dict[str, Any]:
    return {"to": "manager", "intake": {"type": "ephemeral", "scope": "project"}, **fields}


def _classify(text: str, payload: dict[str, Any] | None, **kwargs: Any) -> tuple[Any, dict[str, str]]:
    """Classify ``text`` with a model that hands off ``payload`` (None: hands off nothing)."""
    seen: dict[str, str] = {}

    def run_exec(prompt: str, actions: Any) -> Any:
        seen["prompt"] = prompt
        if payload is not None:
            try:
                actions.dispatch("hand_off", payload)
            except ValueError as exc:
                seen["refused"] = str(exc)
        return SimpleNamespace(exit_code=0)

    return classify_front_door(text, run_exec=run_exec, **kwargs), seen


def test_front_door_prompt_leaves_the_fields_to_the_form() -> None:
    prompt = build_front_door_prompt("你好", active_mission=True)
    schema = str(form_tool("front_door"))

    # The highest-frequency model call: the prompt carries the message and
    # the run's facts; every field is defined once, in the form.
    assert len(prompt) <= 800
    assert RESEARCHER_VOICE_BRIEF in prompt and RESEARCHER_VOICE not in prompt
    assert "ACTIVE_MISSION: YES" in prompt
    assert "Decision:" not in prompt and "CONFIG" not in prompt
    for phrase in (
        "conversation, status, a quick inspection", "however big", "以后都用中文",
        "explicit continue or resume after a pause is to=team", "forbid only for an explicit command",
    ):
        assert phrase in schema


def test_only_a_form_that_can_ask_the_operator_is_told_when_to() -> None:
    # The front door cannot hand to the operator; told when to, it answered
    # conflicting requirements itself instead of routing them (replay 2026-10-22).
    assert "operator" not in form_tool("front_door")["inputSchema"]["properties"]["to"]["description"]
    for form in ("engineer", "review", "route", "supervise"):
        assert "contradict each other" in form_tool(form)["inputSchema"]["properties"]["to"]["description"]


def test_front_door_prompt_says_whether_it_may_answer() -> None:
    assert "reply is the complete answer" in build_front_door_prompt("x")
    closed = build_front_door_prompt("x", allow_reply=False)
    assert "Do not answer here except a greeting" in closed
    assert "ACTIVE_MISSION: NO" in closed


def test_classifier_without_reply_consumer_never_delivers_a_reply() -> None:
    (decision, seen) = _classify("Explain SFT data.", _form(self_mode="reply", reply="It is..."))
    assert "Do not answer here" in seen["prompt"]
    assert decision == (None, None, "simple")


def test_reply_and_mode_reach_their_consumers() -> None:
    modes: list[str] = []
    replies: list[str] = []
    decision, _ = _classify(
        "只回复 hello", _form(self_mode="reply", reply="hello", control={"kind": "no_dispatch", "quote": "只回复"}),
        self_mode_sink=modes.append, reply_sink=replies.append,
    )
    assert decision == (None, "no_dispatch", "simple")
    assert modes == ["reply"] and replies == ["hello"]


def test_self_turn_defaults_to_inspection() -> None:
    modes: list[str] = []
    _classify("这个文件在做什么？", _form(), self_mode_sink=modes.append)
    assert modes == ["inspect"]


def test_team_route_takes_its_lifetime_from_the_same_call() -> None:
    lifetimes: list[str] = []
    modes: list[str] = []
    decision, _ = _classify("持续改进这个项目", _form(to="team", lifetime="standing"),
                            lifetime_sink=lifetimes.append, self_mode_sink=modes.append)
    assert decision == (None, None, "complex")
    assert lifetimes == ["standing"] and modes == []
    lifetimes.clear()
    _classify("帮我调研一下", _form(to="team"), lifetime_sink=lifetimes.append)
    assert lifetimes == ["bounded"]


def test_greeting_finishes_from_one_call_only_with_its_reply() -> None:
    greetings: list[str] = []
    _classify("你好", _form(self_mode="reply", greeting=True, reply="你好！"), greeting_sink=greetings.append)
    _classify("你好", _form(self_mode="reply", greeting=True), greeting_sink=greetings.append)
    assert greetings == ["你好！"]


def test_name_axis_reports_the_title() -> None:
    names: list[str] = []
    _classify("帮我简单证明勾股定理", _form(name="勾股定理简证"), name_sink=names.append)
    _classify("你好", _form(), name_sink=names.append)
    assert names == ["勾股定理简证"]


def test_config_entries_are_one_transaction() -> None:
    decision, _ = _classify("换模型并打开安全模式", _form(config=[
        {"knob": "model", "roles": ["planner", "engineer"], "value": "gpt-x"},
        {"knob": "safe_mode", "roles": ["planner"], "value": "on"},
    ]))
    assert decision[0] == (
        ConfigIntent(knob="model", roles=("planner", "engineer"), value="gpt-x"),
        ConfigIntent(knob="safe_mode", roles=(), value="on"),
    )
    single, _ = _classify("模型都用 gpt-x", _form(config=[{"knob": "model", "value": "gpt-x"}]))
    assert single == (ConfigIntent(knob="model", roles=(), value="gpt-x"), None, "simple")


@pytest.mark.parametrize(("payload", "refusal"), [
    (_form(config=[{"knob": "model", "value": "a"}, {"knob": "model", "value": "b"}]), "one entry per setting"),
    (_form(config=[{"knob": "model", "value": "a"}], intake={"type": "standing_directive", "scope": "project"}),
     "intake.type=preference"),
    (_form(config=[{"knob": "colour", "value": "blue"}]), "config.0.knob"),
    (_form(to="team", control={"kind": "pause", "quote": "停一下"}), "to=manager"),
    (_form(to="team", authorization=["acceptance_retry"]), "to=manager"),
    (_form(control={"kind": "abort", "quote": "please abort"}), "control.quote"),
    (_form(control={"kind": "steer", "quote": "停一下", "directive": "x"}), "nothing to steer"),
])
def test_front_door_refuses_a_form_against_its_rules(payload: dict, refusal: str) -> None:
    decision, seen = _classify("停一下", payload)
    assert refusal in seen["refused"]
    assert decision == (None, None, "complex")


def test_steer_needs_the_operators_words_and_a_direction() -> None:
    directives: list[str] = []
    message = "把当前任务改成完整前向 profiling"
    _, seen = _classify(message, _form(control={"kind": "steer", "quote": "改成完整前向 profiling"}),
                        steering_sink=directives.append, active_mission=True)
    assert "control.directive" in seen["refused"]
    decision, _ = _classify(
        message, _form(control={"kind": "steer", "quote": "改成完整前向  Profiling", "directive": "完整前向 profiling"}),
        steering_sink=directives.append, active_mission=True,
    )
    assert decision == (None, "steer", "simple")
    assert directives == ["完整前向 profiling"]


def test_steer_may_make_the_mission_standing_and_set_question_policy() -> None:
    lifetimes: list[str] = []
    policies: list[str] = []
    _classify(
        "Do not ask further questions; keep going on this indefinitely.",
        _form(control={"kind": "steer", "quote": "keep going on this indefinitely", "directive": "Continue."},
              lifetime="standing", operator_question_policy="forbid"),
        lifetime_sink=lifetimes.append, operator_question_policy_sink=policies.append, active_mission=True,
    )
    assert lifetimes == ["standing"] and policies == ["forbid"]


@pytest.mark.parametrize("kind", ["abort", "pause", "no_dispatch"])
def test_controls_are_answered_by_the_manager(kind: str) -> None:
    decision, _ = _classify("停一下", _form(control={"kind": kind, "quote": "停一下"}))
    assert decision == (None, kind, "simple")


def test_explicit_authorization_reaches_its_consumer() -> None:
    grants: list[tuple[str, ...]] = []
    replies: list[str] = []
    decision, _ = _classify(
        "允许重试验收", _form(authorization=["acceptance_retry", "acceptance_retry"], self_mode="reply", reply="好"),
        authorization_sink=grants.append, reply_sink=replies.append,
    )
    assert decision == (None, None, "simple")
    assert grants == [("acceptance_retry",)] and replies == []


def test_intake_and_domain_reach_their_consumers() -> None:
    intakes: list[dict] = []
    domains: list[dict] = []
    _classify(
        "以后都用中文回答", _form(intake={"type": "standing_directive", "scope": "global", "roles": ["manager"],
                                       "preference_value": "用中文回答"}, domain={"vertical": "software"}),
        intake_sink=intakes.append, domain_sink=domains.append,
    )
    assert intakes == [{
        "kind": "standing_directive", "scope": "global", "applies_to_roles": ("manager",),
        "preference_kind": "workflow", "preference_value": "用中文回答", "target_revision": 0,
    }]
    assert domains == [{"action": "none", "vertical": "software"}]


def test_no_handoff_takes_the_team_route_and_applies_nothing() -> None:
    failures: list[str] = []
    intakes: list[dict] = []
    decision, _ = _classify("hello", None, failure_sink=failures.append, intake_sink=intakes.append)
    assert decision == (None, None, "complex")
    assert intakes == [] and failures == ["classifier ended without handing off a decision"]


def test_empty_text_makes_no_model_call() -> None:
    def run_exec(_prompt: str, _actions: Any) -> Any:
        raise AssertionError("no call")

    assert classify_front_door("   ", run_exec=run_exec) == (None, None, "complex")


def test_exec_error_is_safe_default() -> None:
    failures: list[str] = []

    def run_exec(_prompt: str, _actions: Any) -> Any:
        raise RuntimeError("boom")

    assert classify_front_door("x", run_exec=run_exec, failure_sink=failures.append) == (None, None, "complex")
    assert failures == ["RuntimeError: boom"]


def test_sink_exception_records_routing_diagnostic() -> None:
    failures: list[str] = []

    def boom(_value: Any) -> None:
        raise RuntimeError("reply pipe closed")

    decision, _ = _classify("say hello", _form(self_mode="reply", reply="hello"),
                            reply_sink=boom, failure_sink=failures.append)
    assert decision == (None, None, "simple")
    assert any("sink failed" in entry and "reply pipe closed" in entry for entry in failures)


def test_nonzero_exit_preserves_redacted_runner_diagnostic(redact_secrets_on, monkeypatch) -> None:
    result = SimpleNamespace(exit_code=1, fatal_error="", stderr_lines=[
        '"node" is not recognized as an internal or external command.',
        "OPENAI_API_KEY=super-secret-value",
    ])
    failures: list[str] = []
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")

    decision = classify_front_door("hello", run_exec=lambda _prompt, _actions: result, failure_sink=failures.append)

    assert decision == (None, None, "complex")
    assert len(failures) == 1
    assert "exit 1" in failures[0] and "node" in failures[0]
    assert "super-secret-value" not in failures[0]
    assert "<REDACTED" in failures[0]
