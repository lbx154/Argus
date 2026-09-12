"""End-to-end presentation plumbing; supplied model verdicts are not fact checks."""
import copy
from types import SimpleNamespace

from argus_skill.webapi import map_narrative


def document():
    return {"key": "a", "task_id": "a", "task": {
        "title": "Check the available directions", "objective": "Distinguish a bound from an exact count",
        "non_goals": ["A result about every space"],
    }, "events": [{"id": "start-a", "text": "Work started"}]}


def card():
    return {"title": "Check the count", "summary": "The check is running", "detail": "No result is recorded yet",
            "reader_brief": {"why": "Check whether the evidence supports an exact count",
                             "scope": "Work is ongoing; no conclusion is established", "next": "A next step is not recorded",
                             "concept": {"name": "Independent directions", "explanation": "These directions cannot be made from each other",
                                         "example": "Two independent directions show that the dimension is exactly two",
                                         "connection": "The task asks whether the count is exact"}}}


def test_one_draft_and_one_check_share_a_deadline_and_a_cached_check_is_reused(monkeypatch):
    observed = []
    original = card()
    replacement = {**original["reader_brief"]["concept"],
                   "example": "Two independent directions give a lower bound of two; an exact count needs a spanning argument"}

    def run(prompt, schema, _config, **kwargs):
        observed.append((prompt, kwargs["deadline"]))
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(original)}, "relations": []}
        return {"reviews": {"a": {
            "status": "corrected", "reason": "The exact count needs more evidence", "replacement": replacement,
            "findings": [{"field": "example", "quote": "exactly two", "kind": "unsupported_inference",
                          "reason": "Independence only establishes a lower bound"}],
        }}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    kwargs = {"config": SimpleNamespace(revision="model-a"), "project_root": None, "global_root": None}
    result = map_narrative.generate([document()], [{"id": "a"}], "en-US", **kwargs)
    assert len(observed) == 2 and observed[0][1] == observed[1][1]
    saved = result["cards"][0]
    assert saved["reader_brief"]["concept"] == replacement
    assert saved["teaching_review"]["status"] == "corrected"
    assert saved["reader_brief"]["scope"] == original["reader_brief"]["scope"]
    assert result["teaching_reviews"]
    again = map_narrative.generate([document()], [{"id": "a"}], "en-US",
                                   cached_reviews=result["teaching_reviews"], **kwargs)
    assert len(observed) == 3  # A new draft, with no repeated concept review.
    assert again["cards"][0]["reader_brief"]["concept"] == replacement


def test_failed_teaching_check_keeps_task_facts_but_does_not_publish_the_unchecked_example(monkeypatch):
    calls = 0
    original = card()

    def run(_prompt, schema, _config, **_kwargs):
        nonlocal calls
        calls += 1
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(original)}, "relations": []}
        raise OSError("Review unavailable")

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.generate([document()], [{"id": "a"}], "en-US",
                                   config=SimpleNamespace(revision="model-a"), project_root=None, global_root=None)
    assert calls == 2
    saved = result["cards"][0]
    assert saved["reader_brief"]["concept"] is None
    assert saved["reader_brief"]["why"] == original["reader_brief"]["why"]
    assert saved["teaching_review"]["status"] == "unavailable"
    assert result["teaching_reviews"] == {}


def test_narration_context_keeps_current_cards_and_direct_dependencies_without_unrelated_history():
    tasks = [{"id": str(index), "deps": []} for index in range(100)]
    tasks[90]["deps"] = ["3", "7"]
    tasks[3]["deps"] = ["2"]
    before = copy.deepcopy(tasks)
    selected = map_narrative.generation_context_tasks(tasks, [{"task_id": "90"}, {"task_id": "91"}])
    assert [task["id"] for task in selected] == ["90", "91", "3", "7"]
    assert tasks == before


def test_cached_card_does_not_bypass_a_new_model_or_teaching_checker(tmp_path, monkeypatch):
    config = SimpleNamespace(revision="model-a")
    calls = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: config)

    def generate(documents, *_args, **_kwargs):
        calls.append(documents)
        return {"cards": [{**card(), "key": document["key"], "teaching_review": {
            "status": "accepted", "review_version": map_narrative.TEACHING_REVIEW_VERSION,
        }} for document in documents], "relations": []}

    monkeypatch.setattr(map_narrative, "generate", generate)
    dataset = {"id": "live:cache", "tasks": [{"id": "a", "title": "Task", "status": "pending", "deps": []}], "events": []}
    request = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}]
    first = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)["cached"]
    assert len(calls) == 1
    config.revision = "model-b"
    second = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert len(calls) == 2 and second["cards"]["a"]["model_revision"] == "model-b"
    monkeypatch.setattr(map_narrative, "TEACHING_REVIEW_VERSION", 99)
    third = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert len(calls) == 3
    assert first["cards"]["a"]["input_revision"] != third["cards"]["a"]["input_revision"]


def test_later_focused_cards_can_add_relationships_for_their_own_context(tmp_path, monkeypatch):
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: SimpleNamespace(revision="model-a"))
    seen = []

    def generate(documents, tasks, *_args, **_kwargs):
        seen.append([task["id"] for task in tasks])
        return {"cards": [{**card(), "key": document["key"]} for document in documents],
                "relations": [{"source": tasks[1]["id"], "target": tasks[0]["id"], "label": "supports", "evidence": "The task uses this input"}]}

    monkeypatch.setattr(map_narrative, "generate", generate)
    dataset = {"id": "live:relations", "tasks": [
        {"id": "x", "title": "First input", "deps": []}, {"id": "y", "title": "Other input", "deps": []},
        {"id": "a", "title": "First task", "deps": ["x"]}, {"id": "b", "title": "Later task", "deps": ["y"]},
    ], "events": []}
    def request(key):
        return [{"key": key, "task_id": key, "kind": "task", "event_ids": []}]
    map_narrative.enrich(tmp_path, dataset, request("a"), "en-US", project_root=tmp_path)
    cache = map_narrative.read_cache(tmp_path, "live:relations:en-US")
    assert set(cache["relation_tasks"]) == {"a", "x"}
    later = map_narrative.enrich(tmp_path, dataset, request("b"), "en-US", project_root=tmp_path)
    assert seen == [["a", "x"], ["b", "y"]]
    assert {(r["source"], r["target"]) for r in later["relations"]} == {("x", "a"), ("y", "b")}
