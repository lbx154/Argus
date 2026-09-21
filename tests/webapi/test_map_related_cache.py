"""Related teaching inputs do not retain snapshots or trigger new explanations."""

import copy
import json

import pytest

from argus.webapi import map_narrative
from argus.webapi.map_model import MapModel


@pytest.fixture
def cached_neighbors(tmp_path, monkeypatch):
    dataset = {"id": "live:neighbors", "tasks": [
        {"id": "a", "title": "Current task", "objective": "Assess the current result",
         "status": "running", "revision": "a-v1", "deps": ["b"]},
        {"id": "b", "title": "Neighbor", "objective": "Old neighboring goal",
         "status": "pending", "revision": "b-v1", "deps": []},
        {"id": "c", "title": "Unrelated", "objective": "An unrelated goal", "status": "pending", "deps": []},
    ], "events": []}
    request = [{"key": "a:brief", "task_id": "a", "kind": "task", "event_ids": []}]
    now = [1000.0]
    calls = []
    during_draft = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: MapModel("pi", "gpt-5.5", "medium", "argus-pi"))
    monkeypatch.setattr(map_narrative.time, "time", lambda: now[0])

    def run(prompt, schema, _config, **_kwargs):
        calls.append(schema)
        if "cards" in schema["properties"]:
            sources = json.loads(prompt.split("\n研究记录：\n", 1)[1])
            for callback in during_draft:
                callback()
            return {"cards": {"a:brief": {
                "title": "Current explanation", "summary": "No result is established", "detail": "Check the recorded conditions",
                "reader_brief": {"why": "Assess the available evidence", "scope": "The current task is ongoing",
                                 "next": sources["related_tasks"][0]["objective"], "concept": None},
            }}, "relations": []}
        verdict = {"status": "accepted", "reason": "Supplied test verdict", "findings": [], "replacement": None}
        return {section: {key: copy.deepcopy(verdict) for key in schema["properties"][section]["properties"]}
                for section in ("reviews", "readings")}

    monkeypatch.setattr(map_narrative, "run_map_model", run)

    def enrich():
        return map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)

    saved = copy.deepcopy(enrich()["cards"]["a:brief"])
    assert len(calls) == 2
    assert "source_snapshot" not in saved
    assert saved["reader_brief"]["next"] == "Old neighboring goal"
    return dataset, now, calls, during_draft, saved, enrich


@pytest.mark.parametrize("change", [
    {"objective": "New neighboring goal"}, {"title": "Revised neighbor"},
    {"status": "cancelled"}, {"deps": ["c"]}, None,
], ids=["goal", "title", "cancel", "deps", "delete"])
def test_neighbor_changes_do_not_regenerate_the_selected_explanation(cached_neighbors, change):
    dataset, now, calls, _, saved, enrich = cached_neighbors
    own_task = copy.deepcopy(dataset["tasks"][0])
    if change is None:
        dataset["tasks"].pop(1)
    else:
        dataset["tasks"][1].update(change)
    coalesced = enrich()
    assert coalesced["cached"] is True
    assert coalesced["cards"]["a:brief"] == saved and len(calls) == 2
    now[0] += 26
    refreshed = enrich()
    assert refreshed["cached"] is True and len(calls) == 2
    assert refreshed["cards"]["a:brief"] == saved
    assert dataset["tasks"][0] == own_task


@pytest.mark.parametrize("target, change", [
    (1, {"revision": "b-v2", "summary": "Later progress", "updated_ts": 1200}),
    (1, {"acceptance_check": "A field not supplied as related context"}),
    (2, {"objective": "Changed unrelated goal", "status": "cancelled", "deps": ["a"]}),
], ids=["neighbor-progress", "unprovided-field", "unrelated-task"])
def test_other_changes_do_not_reselect_neighbors_or_call_models(cached_neighbors, target, change):
    dataset, now, calls, _, saved, enrich = cached_neighbors
    dataset["tasks"][target].update(change)
    now[0] += 26
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == saved and len(calls) == 2


def test_neighbor_bounds_and_truncation_do_not_trigger_regeneration(cached_neighbors):
    dataset, now, calls, _, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor.update(objective="🧪" * 500 + "old suffix", deps=[f"dep-{i}" for i in range(25)])
    now[0] += 26
    saved = copy.deepcopy(enrich()["cards"]["a:brief"])
    assert len(calls) == 2
    neighbor.update(objective="🧪" * 500 + "new suffix", deps=[f"dep-{i}" for i in range(24)] + ["different tail"])
    now[0] += 26
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == saved and len(calls) == 2
    neighbor["objective"] = "🧪" * 500
    assert enrich()["cached"] is True and len(calls) == 2


def test_long_source_ids_use_the_same_identity_as_the_model_context(cached_neighbors):
    dataset, now, calls, _, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor["id"] = "🧪" * 161
    dataset["tasks"][0]["deps"] = [neighbor["id"]]
    now[0] += 26
    saved = enrich()["cards"]["a:brief"]
    assert "source_snapshot" not in saved
    assert enrich()["cached"] is True and len(calls) == 2
    neighbor["objective"] = "A changed long-ID neighbor"
    now[0] += 26
    assert enrich()["cached"] is True and len(calls) == 2


def test_teaching_inputs_remain_bound_during_generation_without_retained_snapshot(cached_neighbors):
    dataset, now, calls, during_draft, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor["objective"] = "Goal captured before the call"
    dataset["tasks"][0]["objective"] = "Changed own goal"
    during_draft.append(lambda: neighbor.update(objective="Goal edited while the call ran", status="cancelled"))
    now[0] += 26
    saved = copy.deepcopy(enrich()["cards"]["a:brief"])
    assert saved["reader_brief"]["next"] == "Goal captured before the call"
    assert "source_snapshot" not in saved
    during_draft.clear()
    now[0] += 26
    current = enrich()
    assert current["cached"] is True and len(calls) == 4
    assert current["cards"]["a:brief"] == saved


def test_unavailable_model_keeps_the_explanation_readable(cached_neighbors, monkeypatch):
    dataset, now, calls, _, saved, enrich = cached_neighbors
    dataset["tasks"][1]["status"] = "cancelled"
    now[0] += 26
    monkeypatch.setattr(map_narrative, "configured", lambda: False)
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == saved and len(calls) == 2


@pytest.mark.parametrize("version", [None, 1, 2])
def test_legacy_snapshot_is_not_backfilled_or_forced_to_generate(cached_neighbors, tmp_path, version):
    dataset, now, calls, _, _, enrich = cached_neighbors
    source = "live:neighbors:en-US"
    cache = map_narrative.read_cache(tmp_path, source)
    card = cache["cards"]["a:brief"]
    if version is not None:
        card["source_snapshot"] = {"version": version, "related_tasks": copy.deepcopy(dataset["tasks"][1:2])}
    map_narrative._write_cache(map_narrative.cache_path(tmp_path, source), cache)
    dataset["tasks"][1]["objective"] = "A later neighboring goal"
    now[0] += 26
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == card and len(calls) == 2
    assert map_narrative.read_cache(tmp_path, source) == cache
