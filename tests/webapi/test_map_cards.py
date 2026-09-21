from __future__ import annotations

import sys

import pytest

from argus.webapi import map_cards, map_model


def dataset(status="done"):
    return {"id": "live:s-cards", "tasks": [
        {"id": "t0", "title": "Run RULER retrieval", "objective": "Measure retrieval accuracy", "goal_contribution": "",
         "summary": "released the mission slot", "status": "done", "deps": [],
         "outcome": {"execution_status": "completed", "review_status": "done"}},
        {"id": "t1", "title": "Draft the paper", "objective": "Write it up", "goal_contribution": "",
         "summary": "", "status": status, "deps": []},
    ], "events": [
        {"id": "e0", "type": "round.main.completed", "item_id": "t0", "ts": 1, "text": "An early note."},
        {"id": "e1", "type": "life.mission.completed", "item_id": "t0", "ts": 2, "text": "Accuracy reached 38.31% at 32k."},
        {"id": "e2", "type": "work.segment", "item_id": "t0", "ts": 3, "text": "not a record of the work"},
    ]}


@pytest.fixture
def model(monkeypatch):
    config = map_model.MapModel("pi", "", "medium", sys.executable)
    monkeypatch.setattr(map_cards, "resolve_map_model", lambda: config)
    calls = []

    def answer(cards):
        def run(prompt, output_schema, selected, **kwargs):
            calls.append({"prompt": prompt, "schema": output_schema})
            return {"cards": cards}
        monkeypatch.setattr(map_cards, "run_map_model", run)
    answer.calls = calls
    return answer


def test_reading_never_asks_the_model(tmp_path, model):
    model([{"id": "t0", "title": "x", "summary": "y"}])
    assert map_cards.words(tmp_path, dataset(), ["t0", "t1"], "zh-CN", project_root=tmp_path) == {
        "cards": {}, "available": True}
    assert model.calls == []


def test_writes_once_from_the_works_own_record(tmp_path, model):
    model([
        {"id": "t0", "title": "长文本检索评测", "summary": "32k 下准确率 38.31%。"},
        {"id": "t1", "title": " ", "summary": "no title, dropped"},
        {"id": "ghost", "title": "not asked for", "summary": ""},
    ])
    first = map_cards.words(tmp_path, dataset(), ["t0", "t1", "t0", "ghost"], "zh-CN",
                            project_root=tmp_path, generate=True)
    assert first["cards"] == {"t0": {"title": "长文本检索评测", "summary": "32k 下准确率 38.31%。"}}
    asked = model.calls[0]["prompt"]
    # What the work found comes from its last record, not from the harness's note on the task.
    assert "Accuracy reached 38.31% at 32k." in asked
    assert "released the mission slot" not in asked and "not a record of the work" not in asked
    assert model.calls[0]["schema"]["properties"]["cards"]["items"]["properties"]["id"]["enum"] == ["t0", "t1"]
    again = map_cards.words(tmp_path, dataset(), ["t0"], "zh-CN", project_root=tmp_path, generate=True)
    assert again["cards"] == first["cards"] and len(model.calls) == 1


def test_a_running_task_is_described_by_what_was_asked_and_rewritten_when_it_settles(tmp_path, model):
    model([{"id": "t1", "title": "撰写论文", "summary": "正在起草。"}])
    map_cards.words(tmp_path, dataset("running"), ["t1"], "zh-CN", project_root=tmp_path, generate=True)
    assert '"result"' not in model.calls[0]["prompt"]
    cache = map_cards.read_cache(tmp_path, map_cards.cards_source("live:s-cards", "zh-CN"))
    cache["attempt_at"] = 0
    map_cards._write_cache(map_cards.cache_path(tmp_path, map_cards.cards_source("live:s-cards", "zh-CN")), cache)
    model([{"id": "t1", "title": "撰写论文", "summary": "初稿已编译。"}])
    settled = map_cards.words(tmp_path, dataset("done"), ["t1"], "zh-CN", project_root=tmp_path, generate=True)
    assert settled["cards"]["t1"]["summary"] == "初稿已编译。" and len(model.calls) == 2


def test_without_a_session_or_after_a_failure_nothing_is_asked(tmp_path, model, monkeypatch):
    model([{"id": "t0", "title": "x", "summary": "y"}])
    assert map_cards.words(tmp_path, dataset(), ["t0"], "zh-CN", generate=True) == {"cards": {}, "available": False}

    def fail(*args, **kwargs):
        model.calls.append("failed")
        raise map_model.MapGenerationError("map_timeout")
    monkeypatch.setattr(map_cards, "run_map_model", fail)
    with pytest.raises(map_model.MapGenerationError):
        map_cards.words(tmp_path, dataset(), ["t0"], "zh-CN", project_root=tmp_path, generate=True)
    waiting = map_cards.words(tmp_path, dataset(), ["t0"], "zh-CN", project_root=tmp_path, generate=True)
    assert waiting["retry_after"] > 0 and model.calls == ["failed"]
