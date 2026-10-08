"""A settings change asked for in Chinese is confirmed in Chinese."""

from argus.manager.config_intent import localize_config_confirmation


def test_chinese_operator_gets_a_chinese_confirmation():
    line = "Set all Argus roles' model to fake-model-1."
    out = localize_config_confirmation(line, chinese=True)
    assert out == "已把所有角色的模型改为 fake-model-1，下一次调用起生效。"
    assert localize_config_confirmation(line, chinese=False) == line


def test_role_scoped_and_global_settings_are_localized():
    assert localize_config_confirmation(
        "Set Planner / Engineer reasoning effort to high.", chinese=True
    ) == "已把 Planner / Engineer 的推理强度改为 high。"
    assert localize_config_confirmation(
        "Set ARGUS_SKILL_SAFE_MODE = 1 (on).", chinese=True
    ) == "已设置 ARGUS_SKILL_SAFE_MODE = 1 (on)。"


def test_unknown_lines_pass_through():
    refusal = "“x” is not a model id, so I left the model unchanged."
    assert localize_config_confirmation(refusal, chinese=True) == refusal
