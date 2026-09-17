import copy

import pytest

from argus.webapi import map_narrative
from argus.webapi.map_model import MapModel


@pytest.fixture
def active_map(tmp_path, monkeypatch):
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(map_narrative.time, "time", lambda: clock[0])
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: MapModel("pi", "gpt-5.5", "medium", "argus-pi"))

    def generate(documents, *_args, **_kwargs):
        calls.append(copy.deepcopy(documents))
        return {"cards": [{"key": d["key"], "title": "Survey", "summary": "Reading sources", "detail": "Source text",
                           "teaching_review": {"review_version": map_narrative.TEACHING_REVIEW_VERSION}}
                          for d in documents], "relations": []}

    monkeypatch.setattr(map_narrative, "generate", generate)
    data = {"id": "live:cost", "tasks": [{"id": "a", "title": "Survey", "status": "running", "attempt": 1}], "events": []}
    cards = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}]

    def enrich():
        return map_narrative.enrich(tmp_path, data, cards, "en-US", project_root=tmp_path)

    return clock, calls, data, cards, enrich


def test_tool_progress_does_not_rewrite_or_rebind_checked_copy_until_cooldown(active_map):
    clock, calls, data, cards, enrich = active_map
    first = copy.deepcopy(enrich()["cards"]["a"])
    for elapsed in (60, 120, 240, 599):
        clock[0] = 1000 + elapsed
        data["events"] = [{"id": f"e{elapsed}", "item_id": "a", "text": "Reading another source"}]
        cards[0]["event_ids"] = [f"e{elapsed}"]
        result = enrich()
        assert result["retry_after"] == 600 - elapsed
        assert result["cards"]["a"] == first
    assert len(calls) == 1
    clock[0] = 1600
    assert not enrich()["cached"]
    assert len(calls) == 2


@pytest.mark.parametrize("change", [
    {"status": "done"}, {"status": "failed"}, {"status": "blocked", "pending_question": "Need a dataset"},
    {"attempt": 2}, {"objective": "A different question"},
])
def test_completion_or_changed_task_bypasses_active_cooldown(active_map, change):
    clock, calls, data, cards, enrich = active_map
    enrich()
    clock[0] += 60
    data["tasks"][0].update(change)
    result = enrich()
    assert not result["cached"] and not result.get("retry_after")
    assert len(calls) == 2


def test_deferred_active_card_does_not_hold_up_another_completed_card(active_map):
    clock, calls, data, cards, enrich = active_map
    enrich()
    clock[0] += 60
    data["tasks"][0]["summary"] = "New tool progress"
    data["tasks"].append({"id": "b", "title": "Result", "status": "done"})
    cards.append({"key": "b", "task_id": "b", "kind": "task", "event_ids": []})
    result = enrich()
    assert [d["key"] for d in calls[-1]] == ["b"]
    assert result["cards"]["b"]["task_status"] == "done"
    assert result["retry_after"] == 540
