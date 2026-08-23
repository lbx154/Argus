"""The merged cockpit front-door classifier (life.router.classify_front_door).

ONE model call decides config intent, operator control, and SELF/TEAM routing.
It never chooses a vertical; every formal task goes to Manager classification.
"""
from __future__ import annotations

import pytest

from argus_skill.life.router import (
    ConfigIntent,
    build_front_door_prompt,
    classify_front_door,
)


class _FakeResult:
    def __init__(
        self,
        msg: str,
        exit_code: int = 0,
        role_decisions: list[dict] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg
        self.role_decisions = list(role_decisions or [])


def _exec(answer: str, exit_code: int = 0):
    def run_exec(prompt: str):
        assert all(
            field in prompt
            for field in (
                '"config"', '"control"', '"authorization"', '"steer_directive"',
                '"operator_question_policy"',
                '"route"', '"self_mode"', '"reply"', '"lifetime"', '"greeting"', '"name"',
            )
        )
        return _FakeResult(answer, exit_code)

    return run_exec


def _exec_sequence(*answers: str):
    remaining = list(answers)

    def run_exec(_prompt: str):
        return _FakeResult(remaining.pop(0), 0)

    return run_exec


def test_front_door_prompt_has_a_strict_token_efficiency_budget() -> None:
    prompt = build_front_door_prompt("你好", active_mission=True)

    assert len(prompt) <= 3_000
    assert "live research" in prompt
    assert all(
        field in prompt
        for field in (
            '"config"', '"control"', '"authorization"', '"steer_directive"',
            '"operator_question_policy"',
            '"route"', '"self_mode"', '"reply"', '"lifetime"', '"greeting"', '"name"',
        )
    )
    assert "ARGUS_ROLE_DECISION=" in prompt
    assert "VERTICAL:" not in prompt
    assert "TARGET:" not in prompt
    assert "explicit continue/resume after a pause is not a control token" in prompt
    assert "resumed paused tasks with those effects are TEAM" in prompt
    assert "WORKFLOW:" not in prompt
    assert "FAST_REPLY:" not in prompt
    assert "ACTIVE_MISSION: YES" in prompt
    assert "Questions, requests for an explanation/status/capability check" in prompt
    assert "Ambiguity defaults to no control" in prompt
    assert "FORBID only for an explicit command" in prompt
    assert "ALLOW only when explicitly re-enabled" in prompt
    assert "conversation, status, bounded inspection" in prompt
    assert "finite local task" in prompt
    assert "IMPLEMENT" in prompt
    assert "DEBUG" in prompt
    assert "REVIEW" in prompt
    assert "SYNTHESIZE" in prompt
    assert "BOUNDED_INCREMENT" in prompt
    assert "BOUNDED" in prompt
    assert "STANDING" in prompt
    assert "default BOUNDED" in prompt


def test_front_door_uses_process_decision_without_final_message() -> None:
    result = _FakeResult(
        "",
        role_decisions=[{
            "role": "manager",
            "payload": {
                "config": "NONE",
                "control": "NONE",
                "authorization": "NONE",
                "steer_directive": "NONE",
                "route": "SELF",
                "self_mode": "REPLY",
                "reply": "hello",
                "lifetime": "NONE",
                "greeting": "NONE",
                "name": "Reply",
            },
        }],
    )
    replies: list[str] = []

    decision = classify_front_door(
        "say hello",
        run_exec=lambda _prompt: result,
        reply_sink=replies.append,
    )

    assert decision == (None, None, "simple")
    assert replies == ["hello"]


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


def test_front_door_selects_tool_free_reply_for_message_only_self_turn() -> None:
    modes: list[str] = []
    replies: list[str] = []
    decision = classify_front_door(
        "只回复 hello",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NO_DISPATCH\nROUTE: SELF\n"
            'SELF_MODE: REPLY\nREPLY: "hello"\nLIFETIME: NONE\n'
            "GREETING: NONE\nNAME: Reply"
        ),
        self_mode_sink=modes.append,
        reply_sink=replies.append,
    )

    assert decision == (None, "no_dispatch", "simple")
    assert modes == ["reply"]
    assert replies == ["hello"]


def test_front_door_defaults_self_turn_to_inspection() -> None:
    modes: list[str] = []
    decision = classify_front_door(
        "inspect current files",
        run_exec=_exec("CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\nNAME: Inspect"),
        self_mode_sink=modes.append,
    )

    assert decision == (None, None, "simple")
    assert modes == ["inspect"]


@pytest.mark.parametrize(
    ("token", "mode"),
    [
        ("MICRO", "micro"),
        ("IMPLEMENT", "implement"),
        ("DEBUG", "debug"),
        ("REVIEW", "review"),
        ("SYNTHESIZE", "synthesize"),
    ],
)
def test_front_door_selects_local_worker_mode(token: str, mode: str) -> None:
    modes: list[str] = []
    decision = classify_front_door(
        "complete one finite local task",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: SELF\n"
            f"SELF_MODE: {token}\nREPLY: NONE\nLIFETIME: NONE\n"
            "GREETING: NONE\nNAME: Local task"
        ),
        self_mode_sink=modes.append,
    )

    assert decision == (None, None, "simple")
    assert modes == [mode]


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


def test_front_door_preserves_bounded_lifetime_for_team() -> None:
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
    assert lifetimes == ["bounded"]


def test_front_door_preserves_explicit_bounded_increment_for_team() -> None:
    lifetimes: list[str] = []
    decision = classify_front_door(
        "只完成 research 阶段，不要进入后续阶段",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM\n"
            "LIFETIME: BOUNDED_INCREMENT\nNAME: Research 阶段"
        ),
        lifetime_sink=lifetimes.append,
    )

    assert decision == (None, None, "complex")
    assert lifetimes == ["bounded_increment"]


def test_front_door_missing_lifetime_defaults_team_to_bounded() -> None:
    lifetimes: list[str] = []
    decision = classify_front_door(
        "continue useful work",
        run_exec=_exec("CONFIG: NONE\nCONTROL: NONE\nROUTE: TEAM\nNAME: Work"),
        lifetime_sink=lifetimes.append,
    )

    assert decision == (None, None, "complex")
    assert lifetimes == ["bounded"]


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


def test_front_door_parses_multiple_config_sets_as_one_transaction() -> None:
    intent, control, route = classify_front_door(
        "请把所有模型的 backend 换成 pi，模型用 gpt5.6sol",
        run_exec=_exec(
            "CONFIG: SET backend ALL pi; SET model ALL gpt5.6sol\n"
            "CONTROL: NONE\nROUTE: SELF"
        ),
    )

    assert intent == (
        ConfigIntent(knob="backend", roles=(), value="pi"),
        ConfigIntent(knob="model", roles=(), value="gpt5.6sol"),
    )
    assert control is None
    assert route == "simple"


def test_front_door_rejects_whole_config_batch_when_one_clause_is_malformed() -> None:
    intent, _, _ = classify_front_door(
        "change two settings",
        run_exec=_exec(
            "CONFIG: SET backend ALL pi; malformed model clause\n"
            "CONTROL: NONE\nROUTE: SELF"
        ),
    )

    assert intent is None


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


def test_pause_control_clocks_out_whole_session() -> None:
    intent, control, route = classify_front_door(
        "请暂停整个会话",
        run_exec=_exec("CONFIG: NONE\nCONTROL: PAUSE\nROUTE: TEAM"),
    )
    assert intent is None
    assert control == "pause"
    assert route == "simple"


def test_bare_pause_is_decided_by_front_door_model_call() -> None:
    called = False

    def run_exec(prompt: str):
        nonlocal called
        called = True
        assert "停一下，你在干什么？" in prompt
        return _FakeResult("CONFIG: NONE\nCONTROL: PAUSE\nROUTE: TEAM")

    decision = classify_front_door(
        "停一下，你在干什么？",
        run_exec=run_exec,
        active_mission=True,
    )

    assert decision == (None, "pause", "simple")
    assert called is True


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
    lifetimes: list[str] = []
    intent, control, route = classify_front_door(
        "停止当前自创路线，改为先上网查别人怎么解决这个问题",
        run_exec=_exec_sequence(
            "CONFIG: NONE\nCONTROL: STEER\n"
            "STEER_DIRECTIVE: 暂停当前自创路线；检索最接近的前人方法和基础理论，形成来源审计后由 Planner 决定下一证明节点。\n"
            "ROUTE: TEAM\n"
            "LIFETIME: STANDING\nNAME: 调整数学方向",
            "STEER",
        ),
        steering_sink=directives.append,
        lifetime_sink=lifetimes.append,
        active_mission=True,
    )
    assert intent is None
    assert control == "steer"
    assert route == "simple"
    assert directives == [
        "暂停当前自创路线；检索最接近的前人方法和基础理论，形成来源审计后由 Planner 决定下一证明节点。"
    ]
    assert lifetimes == ["standing"]


@pytest.mark.parametrize(
    ("message", "policy"),
    [
        ("Do not ask further questions; continue autonomously.", "forbid"),
        ("Questions are allowed again.", "allow"),
    ],
)
def test_structured_steer_records_explicit_operator_question_policy(
    message: str,
    policy: str,
) -> None:
    policies: list[str] = []
    responses = [
        _FakeResult(
            "",
            role_decisions=[{
                "role": "manager",
                "payload": {
                    "config": "NONE",
                    "control": "STEER",
                    "authorization": "NONE",
                    "steer_directive": "Continue autonomously without questions.",
                    "operator_question_policy": policy,
                    "route": "SELF",
                    "self_mode": "INSPECT",
                    "reply": "NONE",
                    "lifetime": "NONE",
                    "greeting": "NONE",
                    "name": "Autonomous continuation",
                },
            }],
        ),
        _FakeResult("STEER"),
    ]

    decision = classify_front_door(
        message,
        run_exec=lambda _prompt: responses.pop(0),
        operator_question_policy_sink=policies.append,
        active_mission=True,
    )

    assert decision == (None, "steer", "simple")
    assert policies == [policy]


def test_question_about_profiling_cannot_mutate_active_mission() -> None:
    directives: list[str] = []
    intent, control, route = classify_front_door(
        "有没有完整 Profile 测一下前向耗时？macOS 有 quant activation 吗？",
        run_exec=_exec_sequence(
            "CONFIG: NONE\nCONTROL: STEER\n"
            "STEER_DIRECTIVE: 改成完整前向 profiling。\n"
            "ROUTE: SELF\nSELF_MODE: INSPECT\nREPLY: NONE\n"
            "LIFETIME: NONE\nGREETING: NONE\nNAME: 前向性能问题",
            "SELF",
        ),
        steering_sink=directives.append,
        active_mission=True,
    )

    assert intent is None
    assert control is None
    assert route == "simple"
    assert directives == []


def test_steer_is_structurally_impossible_without_active_mission() -> None:
    directives: list[str] = []
    intent, control, route = classify_front_door(
        "把当前任务改成完整前向 profiling",
        run_exec=_exec(
            "CONFIG: NONE\nCONTROL: STEER\n"
            "STEER_DIRECTIVE: 改成完整前向 profiling。\n"
            "ROUTE: SELF\nSELF_MODE: INSPECT\nREPLY: NONE\n"
            "LIFETIME: NONE\nGREETING: NONE\nNAME: 前向性能任务"
        ),
        steering_sink=directives.append,
        active_mission=False,
    )

    assert intent is None
    assert control is None
    assert route == "simple"
    assert directives == []


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
