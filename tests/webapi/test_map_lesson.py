"""Source-first transport checks; supplied text is not a teaching assessment."""

import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from argus_skill.webapi import map_learning, map_lesson, map_narrative
from argus_skill.webapi.map_model import MapModel


def outline(learning_path=False):
    value = {
        "background_question": "A supplied background question for a transport test",
        "essential_concepts": [{"term": "Test concept", "meaning": "Supplied meaning", "needed_for": "The question"}],
        "recorded_claim": "The source reports a bounded result",
        "recorded_reasoning": "A source-attributed reasoning note",
        "scope_and_limits": "A stated boundary",
        "status_and_next": "No later assignment is supplied",
    }
    if learning_path:
        value = {
            "target_statement": "A supplied result for a transport test",
            "question": "A supplied underlying question",
            "objects": "Two supplied quantities", "operation": "Count the supplied objects",
            "comparison": "Compare the counts", "proof_role": "A source-attributed auxiliary method",
            "core_case": {"given": "Two supplied groups", "worked_result": "Their supplied counts",
                          "new_given": "A third supplied group", "new_result": "Its supplied count",
                          "limit": "Transport fixture, not a teaching assessment"},
            "prerequisites": ["What is being counted?", "Which counts are being compared?"],
        }
    return value


def card(learning_path=False):
    value = {
        "title": "Supplied lesson", "summary": "Supplied summary", "detail": "Supplied formal detail",
        "reader_brief": {"why": "A supplied question", "scope": "A supplied boundary", "next": "A supplied action",
                         "concept": {"name": "A concept", "explanation": "A definition", "example": "An example",
                                     "connection": "Its connection"}},
    }
    if learning_path:
        value["reader_brief"]["concept"] = None
        value["learning_path"] = {"question": "A supplied question", "steps": [{
            "title": title, "explanation": "A supplied rule", "example": "A supplied example",
            "check": {"question": "A supplied check", "answer": "A supplied answer"},
        } for title in ("First operation", "Second operation")]}
    return value


def model():
    return replace(MapModel("pi", "gpt-5.5", "medium", "argus-pi"), review_effort="high")


def run_stub(calls, *, fail_second=False, learning_path=False):
    def run(prompt, schema, config, **kwargs):
        if kwargs.get("on_progress") is not None:
            kwargs["on_progress"](kwargs["phase"])
        sources = json.loads(prompt.rsplit("Retained sources:\n", 1)[1])
        calls.append({"sources": sources, "schema": schema, "config": config, **kwargs})
        keys = list(sources["passages"])
        if "outlines" in schema["properties"]:
            result = {"outlines": {key: outline(learning_path) for key in keys}, "relations": []}
        else:
            if fail_second:
                raise OSError("supplied second-stage failure")
            notes = json.loads(prompt.split("Model-authored source notes:\n", 1)[1].split("\nRetained sources:\n", 1)[0])
            assert notes == {"outlines": {key: outline(learning_path) for key in keys}, "relations": []}
            result = {"cards": {key: card(learning_path) for key in keys}}
        Draft202012Validator(schema).validate(result)
        return result
    return run


@pytest.mark.parametrize("learning_path", [False, True])
def test_two_stages_share_exact_bounded_sources_deadline_and_actual_efforts(monkeypatch, learning_path):
    docs = [{"key": "a", "task_id": "a", "task": {"title": "Source title", "objective": "Source objective"},
             "events": [{"id": "e", "text": "x" * 1700, "next_action": "A saved handoff"}]}]
    tasks = [{"id": "a", "title": "Source title"}, {"id": "b", "objective": "A related goal", "status": "running"}]
    before = copy.deepcopy((docs, tasks))
    calls = []
    phases = []
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, learning_path=learning_path))
    value = map_lesson.generate_source_first(docs, tasks, "en-US", config=model(), project_root=None,
                                              global_root=None, learning_path=learning_path, on_progress=phases.append)
    assert len(calls) == 2 and calls[0]["deadline"] == calls[1]["deadline"]
    assert phases == ["planning", "writing"]
    assert [call["config"].effort for call in calls] == ["medium", "high"]
    assert calls[0]["sources"] == calls[1]["sources"]
    sent = calls[0]["sources"]["passages"]["a"]
    assert sent["events"][0]["text_truncated"] is True
    assert sent["related_task_ids"] == ["b"]
    saved = value["cards"][0]
    snapshot = saved["source_snapshot"]
    assert snapshot["task"] == sent["task"] and snapshot["events"] == sent["events"]
    assert snapshot["related_tasks"] == calls[0]["sources"]["related_tasks"]
    assert saved["teaching_process"]["outline"] == outline(learning_path)
    assert saved["teaching_process"]["kind"] == ("learning_plan_then_lesson" if learning_path else "source_outline_then_lesson")
    assert "teaching_review" not in saved
    assert all(saved[key] == card(learning_path)[key] for key in card(learning_path))
    assert (docs, tasks) == before


def dataset():
    return {"id": "live:s", "tasks": [{"id": "a", "title": "Recorded task", "objective": "Recorded work",
                                      "status": "running", "revision": "r1"}],
            "events": [{"id": "e", "item_id": "a", "type": "life.mission.started", "text": "Started", "ts": 1}]}


@pytest.mark.parametrize("fail_second", [False, True])
@pytest.mark.parametrize("learning_path", [False, True])
def test_preview_caches_only_complete_lessons_separately_and_reuses_them(tmp_path, monkeypatch, fail_second, learning_path):
    calls = []
    phases = []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, fail_second=fail_second, learning_path=learning_path))
    monkeypatch.setattr(map_narrative, "generate", lambda *a, **k: pytest.fail("preview called the legacy pipeline"))
    normal_path = map_narrative.cache_path(tmp_path, map_narrative.copy_source("live:s", "en-US"))
    normal_path.parent.mkdir()
    normal_path.write_text('{"cards":{"retained":"unchanged normal cache"}}')
    before = normal_path.read_bytes()
    if learning_path:
        earlier = map_narrative.cache_path(tmp_path, map_narrative.copy_source("live:s", "en-US", preview=True))
        earlier.write_text('{"cards":{"retained":"original source-first experiment"}}')
        earlier_before = earlier.read_bytes()
    data = dataset()
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]

    def generate():
        return map_narrative.enrich(tmp_path, data, requests, "en-US", project_root=tmp_path,
                                    preview="learning-path" if learning_path else True, on_progress=phases.append)

    if fail_second:
        with pytest.raises(OSError, match="second-stage failure"):
            generate()
        cache = map_narrative.read_cache(tmp_path, map_narrative.copy_source("live:s", "en-US",
                                          preview="learning-path" if learning_path else True))
        assert not cache.get("cards")
        assert phases == ["waiting_for_source", "planning", "writing"]
    else:
        result = generate()
        version = map_learning if learning_path else map_lesson
        assert result["version"] == version.PREVIEW_VERSION
        assert result["cards"]["a"]["teaching_process"]["version"] == version.PROCESS_VERSION
        assert "teaching_review" not in result["cards"]["a"]
        assert generate()["cached"] is True
        assert phases == ["waiting_for_source", "planning", "writing", "waiting_for_source"]
    assert len(calls) == 2
    assert normal_path.read_bytes() == before
    if learning_path:
        assert earlier.read_bytes() == earlier_before


def test_learning_check_requires_an_answer_and_at_most_five_steps():
    value = card(True)["learning_path"]
    value["steps"][0]["check"].pop("answer")
    with pytest.raises(ValueError, match="invalid learning path"):
        map_learning.checked_learning_path(value)
    value = card(True)["learning_path"]
    value["steps"] *= 3
    with pytest.raises(ValueError, match="invalid learning path"):
        map_learning.checked_learning_path(value)
    assert map_learning.checked_learning_path(None) is None


def test_previous_learning_preview_is_retained_on_failure_then_replaced_by_new_generation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, learning_path=True))
    request = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]

    def generate():
        return map_narrative.enrich(tmp_path, dataset(), request, "en-US", project_root=tmp_path,
                                    preview="learning-path")

    with monkeypatch.context() as previous_version:
        previous_version.setattr(map_learning, "PREVIEW_VERSION", 25)
        previous_version.setattr(map_learning, "PROCESS_VERSION", 2)
        generate()
    path = map_narrative.cache_path(tmp_path, map_narrative.copy_source("live:s", "en-US", preview="learning-path"))
    prior = json.loads(path.read_text())
    calls.clear()
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, learning_path=True, fail_second=True))
    with pytest.raises(OSError, match="second-stage failure"):
        generate()
    failed = json.loads(path.read_text())
    assert len(calls) == 2 and failed["cards"] == prior["cards"]
    assert failed["generated_at"] == prior["generated_at"]
    calls.clear()
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, learning_path=True))
    result = generate()
    assert len(calls) == 2 and result["cached"] is False
    assert result["cards"]["a"]["version"] == 26
    assert result["cards"]["a"]["teaching_process"]["version"] == 3
