import pytest

from argus.roles.prompts.manager import build_quick_reply_prompt, build_simple_prompt
from argus.roles.prompts.voice import RESEARCHER_VOICE, RESEARCHER_VOICE_BRIEF


@pytest.mark.parametrize("build", [build_quick_reply_prompt, build_simple_prompt])
def test_ordinary_reader_rules_reach_actual_reply_prompts(build) -> None:
    prompt = build(objective="Explain the result")
    assert RESEARCHER_VOICE in prompt
    assert "ordinary reader" in prompt
    assert "what it means for the person" in prompt
    assert "first use" in prompt
    assert "numbers, sources or uncertainty" in prompt
    assert "professional detail" in prompt
    assert "without a forced template" in prompt
    assert "Never invent progress, completion" in prompt


def test_brief_voice_preserves_readability_and_uncertainty() -> None:
    assert "ordinary reader" in RESEARCHER_VOICE_BRIEF
    assert "explain necessary terms" in RESEARCHER_VOICE_BRIEF
    assert "preserve uncertainty" in RESEARCHER_VOICE_BRIEF
    assert "only when they need to act" in RESEARCHER_VOICE_BRIEF
