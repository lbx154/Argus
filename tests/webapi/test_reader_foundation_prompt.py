"""Input/output contract checks, not a claim of generated teaching quality."""

import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from argus_skill.webapi.reader_foundation_prompt import QUESTION_MARKER, foundation_request


@pytest.mark.parametrize("locale,language", [("zh-CN", "简体中文"), ("en-US", "English")])
def test_preserves_the_actual_submitted_question_without_task_context(locale, language):
    question = '请解释“两个记录相等”是什么意思。\nA 与 B 的规则不同，可以直接相加吗？'
    prompt, schema = foundation_request(question, locale)

    instructions, supplied = prompt.rsplit(QUESTION_MARKER, 1)
    assert json.loads(supplied) == question
    assert language in instructions
    assert set(schema["properties"]) == {"title", "markdown"}
    assert "Retained sources:" not in prompt
    assert "source_task_id" not in prompt


def test_task_or_event_payloads_are_not_accepted_as_foundation_inputs():
    with pytest.raises(TypeError):
        foundation_request("Explain the original question", "en-US", task={"id": "later-task"})
    with pytest.raises(TypeError):
        foundation_request("Explain the original question", "en-US", events=[{"text": "A proof trace"}])


def test_native_output_is_a_standalone_document_with_space_for_needed_foundations():
    _, schema = foundation_request("Explain how to compare these quantities", "en-US")
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    validator.validate({"title": "A foundation document", "markdown": "x" * 32_000})
    for invalid in (
        {"title": "A title only"},
        {"title": "Document", "markdown": ""},
        {"title": "Document", "markdown": "x" * 32_001},
        {"title": "Document", "markdown": "A complete explanation.", "cards": {}},
        {"title": "Document", "markdown": "A complete explanation.", "recorded_reasoning": "A task trace"},
    ):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
