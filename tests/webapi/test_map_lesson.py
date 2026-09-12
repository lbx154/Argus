"""Source-first transport checks; supplied text is not a teaching assessment."""

import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from argus_skill.webapi import map_lesson, map_narrative
from argus_skill.webapi.map_model import MapModel


def outline():
    return {
        "background_question": "A supplied background question for a transport test",
        "essential_concepts": [{"term": "Test concept", "meaning": "Supplied meaning", "needed_for": "The question"}],
        "recorded_claim": "The source reports a bounded result",
        "recorded_reasoning": "A source-attributed reasoning note",
        "scope_and_limits": "A stated boundary",
        "status_and_next": "No later assignment is supplied",
    }


def card():
    return {
        "title": "Supplied lesson", "summary": "Supplied summary", "detail": "Supplied formal detail",
        "reader_brief": {"why": "A supplied question", "scope": "A supplied boundary", "next": "A supplied action",
                         "concept": {"name": "A concept", "explanation": "A definition", "example": "An example",
                                     "connection": "Its connection"}},
    }


def model():
    return replace(MapModel("pi", "gpt-5.5", "medium", "argus-pi"), review_effort="high")


def run_stub(calls, *, fail_second=False):
    def run(prompt, schema, config, **kwargs):
        sources = json.loads(prompt.rsplit("Retained sources:\n", 1)[1])
        calls.append({"sources": sources, "schema": schema, "config": config, **kwargs})
        keys = list(sources["passages"])
        if "outlines" in schema["properties"]:
            result = {"outlines": {key: outline() for key in keys}, "relations": []}
        else:
            if fail_second:
                raise OSError("supplied second-stage failure")
            notes = json.loads(prompt.split("Model-authored source notes:\n", 1)[1].split("\nRetained sources:\n", 1)[0])
            assert notes == {"outlines": {key: outline() for key in keys}, "relations": []}
            result = {"cards": {key: card() for key in keys}}
        Draft202012Validator(schema).validate(result)
        return result
    return run


def test_two_stages_share_exact_bounded_sources_deadline_and_actual_efforts(monkeypatch):
    docs = [{"key": "a", "task_id": "a", "task": {"title": "Source title", "objective": "Source objective"},
             "events": [{"id": "e", "text": "x" * 1700, "next_action": "A saved handoff"}]}]
    tasks = [{"id": "a", "title": "Source title"}, {"id": "b", "objective": "A related goal", "status": "running"}]
    before = copy.deepcopy((docs, tasks))
    calls = []
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls))
    value = map_lesson.generate_source_first(docs, tasks, "en-US", config=model(), project_root=None, global_root=None)
    assert len(calls) == 2 and calls[0]["deadline"] == calls[1]["deadline"]
    assert [call["config"].effort for call in calls] == ["medium", "high"]
    assert calls[0]["sources"] == calls[1]["sources"]
    sent = calls[0]["sources"]["passages"]["a"]
    assert sent["events"][0]["text_truncated"] is True
    assert sent["related_task_ids"] == ["b"]
    saved = value["cards"][0]
    snapshot = saved["source_snapshot"]
    assert snapshot["task"] == sent["task"] and snapshot["events"] == sent["events"]
    assert snapshot["related_tasks"] == calls[0]["sources"]["related_tasks"]
    assert saved["teaching_process"]["outline"] == outline()
    assert saved["teaching_process"]["kind"] == "source_outline_then_lesson"
    assert "teaching_review" not in saved
    assert all(saved[key] == card()[key] for key in card())
    assert (docs, tasks) == before


def dataset():
    return {"id": "live:s", "tasks": [{"id": "a", "title": "Recorded task", "objective": "Recorded work",
                                      "status": "running", "revision": "r1"}],
            "events": [{"id": "e", "item_id": "a", "type": "life.mission.started", "text": "Started", "ts": 1}]}


@pytest.mark.parametrize("fail_second", [False, True])
def test_preview_caches_only_complete_lessons_separately_and_reuses_them(tmp_path, monkeypatch, fail_second):
    calls = []
    monkeypatch.setattr(map_narrative, "resolve_map_model", model)
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_lesson, "run_map_model", run_stub(calls, fail_second=fail_second))
    monkeypatch.setattr(map_narrative, "generate", lambda *a, **k: pytest.fail("preview called the legacy pipeline"))
    normal_path = map_narrative.cache_path(tmp_path, map_narrative.copy_source("live:s", "en-US"))
    normal_path.parent.mkdir()
    normal_path.write_text('{"cards":{"retained":"unchanged normal cache"}}')
    before = normal_path.read_bytes()
    data = dataset()
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["e"]}]

    def generate():
        return map_narrative.enrich(tmp_path, data, requests, "en-US", project_root=tmp_path, preview=True)

    if fail_second:
        with pytest.raises(OSError, match="second-stage failure"):
            generate()
        cache = map_narrative.read_cache(tmp_path, map_narrative.copy_source("live:s", "en-US", preview=True))
        assert not cache.get("cards")
    else:
        result = generate()
        assert result["version"] == map_lesson.PREVIEW_VERSION
        assert result["cards"]["a"]["teaching_process"]["version"] == map_lesson.PROCESS_VERSION
        assert "teaching_review" not in result["cards"]["a"]
        assert generate()["cached"] is True
    assert len(calls) == 2
    assert normal_path.read_bytes() == before
