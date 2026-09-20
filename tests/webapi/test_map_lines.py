from __future__ import annotations

import sys

import pytest

from argus.webapi import map_lines, map_model


def dataset(statuses=("done", "done", "done", "running")):
    return {"id": "live:s-lines", "tasks": [
        {"id": f"t{i}", "title": f"Task {i}", "objective": f"Goal {i}", "goal_contribution": "",
         "summary": f"Result {i}", "status": status, "deps": []}
        for i, status in enumerate(statuses)
    ], "events": []}


PAIRS = [{"source": "t0", "target": "t1"}, {"source": "t1", "target": "t2"}, {"source": "t2", "target": "t3"}]


@pytest.fixture
def model(monkeypatch):
    """A configured map model whose answers the test supplies."""
    config = map_model.MapModel("pi", "", "medium", sys.executable)
    monkeypatch.setattr(map_lines, "resolve_map_model", lambda: config)
    calls = []

    def answer(lines):
        def run(prompt, output_schema, selected, **kwargs):
            calls.append({"prompt": prompt, "schema": output_schema})
            return {"lines": lines}
        monkeypatch.setattr(map_lines, "run_map_model", run)
    answer.calls = calls
    return answer


def test_reading_never_asks_the_model(tmp_path, model):
    model([{"source": "t0", "target": "t1", "label": "baseline results", "evidence": "t1 reads t0's results"}])
    result = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path)
    assert result == {"lines": [], "available": True}
    assert model.calls == []


def test_writes_once_and_remembers_the_lines_with_nothing_to_say(tmp_path, model):
    model([
        {"source": "t0", "target": "t1", "label": "baseline results", "evidence": "t1 reads t0's results"},
        # Not a line that was asked about, a repeat, and an empty label: all dropped.
        {"source": "t0", "target": "t3", "label": "invented", "evidence": "x"},
        {"source": "t0", "target": "t1", "label": "again", "evidence": "x"},
        {"source": "t1", "target": "t2", "label": " ", "evidence": "x"},
    ])
    first = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert first["lines"] == [
        {"source": "t0", "target": "t1", "label": "baseline results", "evidence": "t1 reads t0's results"},
    ]
    assert len(model.calls) == 1
    asked = model.calls[0]
    assert asked["schema"]["properties"]["lines"]["maxItems"] == 3
    # A running task is described by what was asked of it; its result is still moving.
    assert "Result 2" in asked["prompt"] and "Result 3" not in asked["prompt"]

    # Every asked line is settled, including the two the model had nothing to say about.
    again = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert again["lines"] == first["lines"] and len(model.calls) == 1
    read = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path)
    assert read["lines"] == first["lines"]


def test_a_task_settling_asks_again_for_its_lines_only(tmp_path, model, monkeypatch):
    model([])
    map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert len(model.calls) == 1
    model([{"source": "t2", "target": "t3", "label": "figures", "evidence": "t3 uses t2's figures"}])
    settled = dataset(("done", "done", "done", "done"))
    # The reader still has the map open: not asked again straight away.
    waiting = map_lines.notes(tmp_path, settled, PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert waiting["retry_after"] > 0 and len(model.calls) == 1
    cache = map_lines.read_cache(tmp_path, map_lines.lines_source(settled["id"], "en-US"))
    cache["attempt_at"] = 0
    map_lines._write_cache(map_lines.cache_path(tmp_path, map_lines.lines_source(settled["id"], "en-US")), cache)
    result = map_lines.notes(tmp_path, settled, PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert [line["label"] for line in result["lines"]] == ["figures"]
    assert len(model.calls) == 2
    assert '"pairs": [{"source": "t2", "target": "t3"}]' in model.calls[1]["prompt"]


def test_unknown_backward_and_repeated_pairs_are_not_asked_about(tmp_path, model):
    model([])
    pairs = [{"source": "t1", "target": "t0"}, {"source": "ghost", "target": "t1"},
             {"source": "t0", "target": "t1"}, {"source": "t0", "target": "t1"}]
    map_lines.notes(tmp_path, dataset(), pairs, "en-US", project_root=tmp_path, generate=True)
    assert '"pairs": [{"source": "t0", "target": "t1"}]' in model.calls[0]["prompt"]


def test_without_a_session_or_a_model_nothing_is_written(tmp_path, model, monkeypatch):
    model([{"source": "t0", "target": "t1", "label": "x", "evidence": "y"}])
    assert map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", generate=True) == {"lines": [], "available": False}

    def missing():
        raise RuntimeError("no model")
    monkeypatch.setattr(map_lines, "resolve_map_model", missing)
    result = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert result == {"lines": [], "available": False} and model.calls == []


def test_a_failed_attempt_is_not_repeated_at_once(tmp_path, model, monkeypatch):
    def fail(*args, **kwargs):
        model.calls.append("failed")
        raise map_model.MapGenerationError("map_timeout")
    monkeypatch.setattr(map_lines, "run_map_model", fail)
    with pytest.raises(map_model.MapGenerationError):
        map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    result = map_lines.notes(tmp_path, dataset(), PAIRS, "en-US", project_root=tmp_path, generate=True)
    assert result["retry_after"] > 0 and model.calls == ["failed"]
