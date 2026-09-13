"""Input/output contract checks, not a claim of generated teaching quality."""

import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from argus_skill.webapi import reader_clarification
from argus_skill.webapi.map_teaching_review import TEACHING_CORE
from argus_skill.webapi.reader_foundation_prompt import QUESTION_MARKER, foundation_request


@pytest.mark.parametrize("locale,language", [("zh-CN", "简体中文"), ("en-US", "English")])
def test_preserves_the_actual_submitted_question_without_task_context(locale, language):
    question = '请解释“两个记录相等”是什么意思。\nA 与 B 的规则不同，可以直接相加吗？'
    prompt, schema = foundation_request(question, locale)

    instructions, supplied = prompt.rsplit(QUESTION_MARKER, 1)
    assert json.loads(supplied) == question
    assert language in instructions
    assert instructions.count(TEACHING_CORE) == 1
    assert set(schema["properties"]) == {"title", "markdown"}
    assert "Retained sources:" not in prompt
    assert "source_task_id" not in prompt


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
@pytest.mark.parametrize("progress", [False, True])
def test_clarification_reuses_the_teaching_contract_without_changing_saved_sources(locale, progress):
    question = r"Explain the normalization and domain of \(Q\), including its assumptions."
    snapshot = {"sources": [{"id": "saved-answer", "version": 1, "markdown": "The selected explanation."}]}
    if progress:
        snapshot["progress_source"] = {
            "card": {"title": "Selected comparison"},
            "source_snapshot": {"events": [{"id": "recorded-event", "text": "A reported comparison."}]},
        }
    before = json.dumps(snapshot, ensure_ascii=False)
    prompt, schema = reader_clarification.clarification_request(question, locale, snapshot)
    instructions, payload = prompt.split(reader_clarification.SOURCES_MARKER, 1)
    sources, submitted = payload.split(reader_clarification.QUESTION_MARKER, 1)

    assert instructions.count(TEACHING_CORE) == 1
    assert json.loads(sources) == snapshot
    assert json.loads(submitted) == question
    assert json.dumps(snapshot, ensure_ascii=False) == before
    assert schema == foundation_request(question, locale)[1]


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
