"""The narrative prompt and bounded card transport preserve readable explanations."""

import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from argus_skill.webapi import map_narrative
from argus_skill.webapi.map_model import MapModel


def _capture_prompt(monkeypatch) -> str:
    captured = {}

    def fake_run(prompt, output_schema, config, **kwargs):
        if "cards" not in output_schema["properties"]:
            return {"reviews": {}, "readings": {"k": {
                "status": "accepted", "reason": "Supplied transport verdict",
                "findings": [], "replacement": None,
            }}}
        captured["prompt"] = prompt
        return {
            "cards": {"k": {"title": "t", "summary": "s", "detail": "d", "reader_brief": {
                "why": "A recorded purpose", "concept": None,
                "scope": "Independent review is not recorded", "next": "Next step not recorded",
            }}},
            "relations": [],
        }

    monkeypatch.setattr(map_narrative, "run_map_model", fake_run)
    map_narrative.generate(
        [{"key": "k", "task_id": "task"}],
        [{"id": "task"}],
        "zh-CN",
        config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"),
        project_root=None,
        global_root=None,
    )
    return captured["prompt"]


def test_prompt_version_bumped_for_readability_rules():
    assert map_narrative.PROMPT_VERSION == 21


def test_prompt_speaks_of_any_kind_of_work_not_only_research(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "演示文稿" in prompt
    assert "不把每件事都写成" in prompt
    assert "工作段落" in prompt


def test_prompt_restates_runner_receipts_as_one_plain_sentence(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "回执" in prompt
    assert "环境变量" in prompt
    assert "换了个新会话接着做" in prompt


def test_prompt_leads_with_the_finding_not_the_activity(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert "发现X不成立" in prompt
    assert "进行了X的检查" in prompt


def test_prompt_teaches_with_concrete_examples_without_defining_jargon_using_more_jargon(monkeypatch):
    prompt = _capture_prompt(monkeypatch)
    assert map_narrative.TEACHING_GUIDANCE in prompt
    assert "Give finite objects or small values" in prompt
    assert "Background teaching is not a finding from this run" in prompt


@pytest.mark.parametrize("field,limit", [("summary", 250), ("detail", 4000)])
def test_model_transport_accepts_the_complete_limit_and_rejects_an_extra_character(monkeypatch, field, limit):
    calls = []

    def run(_prompt, output_schema, _config, **_kwargs):
        calls.append(output_schema)
        if "cards" not in output_schema["properties"]:
            return {"reviews": {}, "readings": {"k": {
                "status": "accepted", "reason": "Supplied transport verdict", "findings": [], "replacement": None,
            }}}
        candidate = {"cards": {"k": {"title": "A recorded task", "summary": "A reported result", "detail": "Exact conditions",
                                       "reader_brief": {"why": "A meaningful question", "scope": "A limited result",
                                                        "next": "No later action is recorded", "concept": None}}}, "relations": []}
        candidate["cards"]["k"][field] = "x" * (limit - 1) + "!"
        validator = Draft202012Validator(output_schema)
        validator.validate(candidate)
        oversized = copy.deepcopy(candidate)
        oversized["cards"]["k"][field] += "!"
        with pytest.raises(ValidationError):
            validator.validate(oversized)
        return candidate

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.generate([{"key": "k", "task_id": "task"}], [{"id": "task"}], "en-US",
                                   config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"), project_root=None, global_root=None)
    assert len(calls) == 2
    assert result["cards"][0][field] == "x" * (limit - 1) + "!"
    assert set(result["cards"][0]["reader_brief"]) == {"why", "scope", "next", "concept"}
