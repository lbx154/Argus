"""The merged cockpit front-door classifier (life.router.classify_front_door).

ONE model call decides config intent, operator control, and SELF/TEAM routing.
It never chooses a vertical; every formal task goes to Manager classification.
"""
from __future__ import annotations

import pytest

from argus_skill.life.router import (
    ConfigIntent,
    build_front_door_prompt,
    classify_config_intent,
    classify_front_door,
)


class _FakeResult:
    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg


def _exec(answer: str, exit_code: int = 0):
    def run_exec(prompt: str):
        assert all(
            label in prompt
            for label in (
                "CONFIG:", "CONTROL:", "AUTHORIZATION:", "STEER_DIRECTIVE:",
                "ROUTE:", "GREETING:", "NAME:",
            )
        )
        return _FakeResult(answer, exit_code)

    return run_exec


def test_front_door_prompt_has_a_strict_token_efficiency_budget() -> None:
    prompt = build_front_door_prompt("你好", active_mission=True)

    assert len(prompt) <= 4_600
    assert all(
        label in prompt
        for label in (
            "CONFIG:", "CONTROL:", "AUTHORIZATION:", "STEER_DIRECTIVE:",
            "ROUTE:", "GREETING:", "NAME:",
        )
    )
    assert "VERTICAL:" not in prompt
    assert "TARGET:" not in prompt
    assert "WORKFLOW:" not in prompt
    assert "FAST_REPLY:" not in prompt
    assert "ACTIVE_MISSION: YES" in prompt
    assert "LIFETIME:" not in prompt
    assert "BOUNDED" not in prompt


def test_name_axis_reports_concise_title_without_changing_route_contract() -> None:
    names: list[str] = []
    decision = classify_front_door(
        "帮我简单证明勾股定理",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\nNAME: 勾股定理简证"
        ),
        name_sink=names.append,
    )

    assert decision == (None, None, "simple")
    assert names == ["勾股定理简证"]


def test_front_door_reuses_team_lifetime_from_the_same_model_call() -> None:
    lifetimes: list[str] = []
    decision = classify_front_door(
        "持续优化尽可能多的 kernel",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM\n"
            "LIFETIME: STANDING\nNAME: Kernel 持续优化"
        ),
        lifetime_sink=lifetimes.append,
    )

    assert decision == (None, None, "complex")
    assert lifetimes == ["standing"]


def test_front_door_ignores_bounded_lifetime_for_team() -> None:
    lifetimes: list[str] = []
    decision = classify_front_door(
        "完成一份报告",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM\n"
            "LIFETIME: BOUNDED\nNAME: 报告"
        ),
        lifetime_sink=lifetimes.append,
    )

    assert decision == (None, None, "complex")
    assert lifetimes == ["standing"]


def test_front_door_pure_greeting_can_finish_from_one_model_call() -> None:
    replies: list[str] = []
    decision = classify_front_door(
        "你好",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\n"
            "LIFETIME: NONE\nGREETING: GREETING\n"
            "NAME: 打招呼"
        ),
        greeting_sink=replies.append,
    )

    assert decision == (None, None, "simple")
    assert replies == ["你好，我是 Argus Manager。"]


def test_front_door_contextual_greeting_does_not_take_one_call_path() -> None:
    replies: list[str] = []
    decision = classify_front_door(
        "你好，项目现在进展怎么样？",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\n"
            "LIFETIME: NONE\nGREETING: NONE\n"
            "NAME: 项目进展"
        ),
        greeting_sink=replies.append,
    )

    assert decision == (None, None, "simple")
    assert replies == []


def test_both_axes_config_and_self() -> None:
    intent, control, route = classify_front_door(
        "用 copilot",
        run_exec=_exec(
            "CONFIG: SET backend ALL copilot\nCONTROL: NONE\nROUTE: SELF"
        ),
    )
    assert intent == ConfigIntent(knob="backend", roles=(), value="copilot")
    assert control is None
    assert route == "simple"


def test_none_config_and_team() -> None:
    intent, control, route = classify_front_door(
        "优化 kernel",
        run_exec=_exec("CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM"),
    )
    assert intent is None
    assert control is None
    assert route == "complex"


def test_role_scoped_config_with_route() -> None:
    intent, control, route = classify_front_door(
        "x",
        run_exec=_exec(
            "CONFIG: SET effort engineer,reviewer high\n"
            "CONTROL: NONE\nROUTE: SELF"
        ),
    )
    assert intent == ConfigIntent(knob="effort", roles=("engineer", "reviewer"), value="high")
    assert control is None
    assert route == "simple"


def test_malformed_config_does_not_corrupt_route() -> None:
    # A garbled CONFIG line → None, but ROUTE still parses independently.
    intent, control, route = classify_front_door(
        "hi",
        run_exec=_exec("CONFIG: total garbage words\nCONTROL: NONE\nROUTE: SELF"),
    )
    assert intent is None
    assert control is None
    assert route == "simple"


def test_missing_route_line_defaults_complex_config_still_parses() -> None:
    intent, control, route = classify_front_door(
        "x", run_exec=_exec("CONFIG: SET model engineer claude-sonnet-5")
    )
    assert intent == ConfigIntent(knob="model", roles=("engineer",), value="claude-sonnet-5")
    assert control is None
    assert route == "complex"  # no ROUTE line → safe default


def test_unrecognized_route_token_is_complex() -> None:
    _, control, route = classify_front_door(
        "x",
        run_exec=_exec("CONFIG: NONE\nCONTROL: NONE\nROUTE: banana"),
    )
    assert control is None
    assert route == "complex"


def test_abort_control_forces_self_and_never_becomes_team_work() -> None:
    intent, control, route = classify_front_door(
        "停止现在的任务",
        run_exec=_exec("CONFIG: NONE\nCONTROL: ABORT\nROUTE: TEAM"),
    )
    assert intent is None
    assert control == "abort"
    assert route == "simple"


def test_explicit_authorization_uses_structured_action_enum_and_forces_self() -> None:
    authorizations: list[tuple[str, ...]] = []
    intent, control, route = classify_front_door(
        "授权修复 validator 并重试一次验收",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\n"
            "AUTHORIZATION: AUTHORIZE validator_repair,acceptance_retry,unknown\n"
            "ROUTE: TEAM"
        ),
        authorization_sink=authorizations.append,
    )

    assert intent is None
    assert control is None
    assert route == "simple"
    assert authorizations == [("validator_repair", "acceptance_retry")]


def test_authorization_question_does_not_create_authority() -> None:
    authorizations: list[tuple[str, ...]] = []
    _, _, route = classify_front_door(
        "我应该授权修复吗？",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nAUTHORIZATION: NONE\nROUTE: SELF"
        ),
        authorization_sink=authorizations.append,
    )

    assert route == "simple"
    assert authorizations == []


def test_steer_control_routes_running_mission_direction_inline() -> None:
    directives: list[str] = []
    intent, control, route = classify_front_door(
        "你好蠢啊，先上网查别人怎么解决这个问题",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: STEER\n"
            "STEER_DIRECTIVE: 暂停当前自创路线；检索最接近的前人方法和基础理论，形成来源审计后由 Planner 决定下一证明节点。\n"
            "ROUTE: TEAM\n"
            "LIFETIME: NONE\nNAME: 调整数学方向"
        ),
        steering_sink=directives.append,
        active_mission=True,
    )
    assert intent is None
    assert control == "steer"
    assert route == "simple"
    assert directives == [
        "暂停当前自创路线；检索最接近的前人方法和基础理论，形成来源审计后由 Planner 决定下一证明节点。"
    ]


@pytest.mark.parametrize(
    "token",
    ["NO_DISPATCH", "NO-DISPATCH", "NO DISPATCH", "NODISPATCH"],
)
def test_no_dispatch_control_forces_self_and_never_becomes_team_work(
    token: str,
) -> None:
    intent, control, route = classify_front_door(
        "只读检查源码，不要派发任务",
        run_exec=_exec(
            f"CONFIG: NONE\nCONTROL: {token}\nROUTE: TEAM"
        ),
    )
    assert intent is None
    assert control == "no_dispatch"
    assert route == "simple"


def test_question_about_stopping_is_not_a_control() -> None:
    _, control, route = classify_front_door(
        "怎么实现停止功能",
        run_exec=_exec("CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM"),
    )
    assert control is None
    assert route == "complex"


def test_empty_text_no_model_call() -> None:
    called = [0]

    def _spy(prompt: str):
        called[0] += 1
        return _FakeResult("CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF")

    intent, control, route = classify_front_door("   ", run_exec=_spy)
    assert (intent, control, route) == (None, None, "complex")
    assert called[0] == 0  # never calls the model on empty input


def test_exec_error_is_safe_default() -> None:
    def _boom(prompt: str):
        raise RuntimeError("backend down")

    assert classify_front_door("y", run_exec=_boom) == (None, None, "complex")


def test_nonzero_exit_is_safe_default() -> None:
    intent, control, route = classify_front_door(
        "y",
        run_exec=_exec(
            "CONFIG: SET backend ALL codex\nCONTROL: NONE\nROUTE: SELF",
            exit_code=1,
        ),
    )
    assert (intent, control, route) == (None, None, "complex")


def test_config_parse_parity_with_classify_config_intent() -> None:
    # The shared _parse_config_line means the merged path and the standalone
    # classifier must produce the SAME ConfigIntent for the same SET line.
    line = "SET effort engineer,reviewer high"
    merged, _, _ = classify_front_door(
        "x",
        run_exec=_exec(f"CONFIG: {line}\nCONTROL: NONE\nROUTE: TEAM"),
    )
    # standalone uses its own (non-merged) prompt, so a plain run_exec here.
    standalone = classify_config_intent("x", run_exec=lambda p: _FakeResult(line))
    assert merged == standalone == ConfigIntent("effort", ("engineer", "reviewer"), "high")


def test_prefixes_are_case_insensitive() -> None:
    intent, control, route = classify_front_door(
        "x",
        run_exec=_exec(
            "config: SET safe_mode - on\ncontrol: none\nroute: self"
        ),
    )
    assert intent == ConfigIntent(knob="safe_mode", roles=(), value="on")
    assert control is None
    assert route == "simple"
