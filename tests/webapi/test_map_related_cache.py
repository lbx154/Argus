"""A card's saved neighboring sources participate in on-demand cache reuse."""

import copy
import json

import pytest

from argus_skill.webapi import map_narrative
from argus_skill.webapi.map_model import MapModel


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
    assert [task["id"] for task in saved["source_snapshot"]["related_tasks"]] == ["b"]
    return dataset, now, calls, during_draft, saved, enrich


@pytest.mark.parametrize("change", [
    {"objective": "New neighboring goal"}, {"title": "Revised neighbor"},
    {"status": "cancelled"}, {"deps": ["c"]}, None,
], ids=["goal", "title", "cancel", "deps", "delete"])
def test_only_neighbor_change_refreshes_after_coalescing_without_relabeling_old_sources(cached_neighbors, change):
    dataset, now, calls, _, saved, enrich = cached_neighbors
    own_task = copy.deepcopy(dataset["tasks"][0])
    if change is None:
        dataset["tasks"].pop(1)
    else:
        dataset["tasks"][1].update(change)
    coalesced = enrich()
    assert coalesced["retry_after"] == 25
    assert coalesced["cards"]["a:brief"] == saved and len(calls) == 2
    now[0] += 26
    refreshed = enrich()
    assert refreshed["cached"] is False and len(calls) == 4
    updated = refreshed["cards"]["a:brief"]
    assert updated["source_snapshot"]["related_tasks"] != saved["source_snapshot"]["related_tasks"]
    assert updated["copy_revision"] > saved["copy_revision"]
    assert dataset["tasks"][0] == own_task  # No own-task revision change was needed.
    assert enrich()["cached"] is True and len(calls) == 4


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


def test_dependency_comparison_uses_the_same_bounded_source_projection(cached_neighbors):
    dataset, now, calls, _, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor.update(objective="🧪" * 500 + "old suffix", deps=[f"dep-{i}" for i in range(25)])
    now[0] += 26
    saved = copy.deepcopy(enrich()["cards"]["a:brief"])
    assert len(calls) == 4
    neighbor.update(objective="🧪" * 500 + "new suffix", deps=[f"dep-{i}" for i in range(24)] + ["different tail"])
    now[0] += 26
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == saved and len(calls) == 4
    # The loss marker itself is part of the material that was supplied.
    neighbor["objective"] = "🧪" * 500
    assert enrich()["cached"] is False and len(calls) == 6


def test_long_source_ids_use_the_same_identity_as_the_model_context(cached_neighbors):
    dataset, now, calls, _, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor["id"] = "🧪" * 161
    dataset["tasks"][0]["deps"] = [neighbor["id"]]
    now[0] += 26
    saved = enrich()["cards"]["a:brief"]
    source = saved["source_snapshot"]["related_tasks"][0]
    assert source["id"] == "🧪" * 160 and source["id_truncated"] is True
    assert enrich()["cached"] is True and len(calls) == 4
    neighbor["objective"] = "A changed long-ID neighbor"
    now[0] += 26
    assert enrich()["cached"] is False and len(calls) == 6


def test_related_snapshot_remains_bound_to_inputs_when_neighbor_changes_during_generation(cached_neighbors):
    dataset, now, calls, during_draft, _, enrich = cached_neighbors
    neighbor = dataset["tasks"][1]
    neighbor["objective"] = "Goal captured before the call"
    during_draft.append(lambda: neighbor.update(objective="Goal edited while the call ran", status="cancelled"))
    now[0] += 26
    saved = copy.deepcopy(enrich()["cards"]["a:brief"])
    assert saved["reader_brief"]["next"] == "Goal captured before the call"
    assert saved["source_snapshot"]["related_tasks"][0]["objective"] == "Goal captured before the call"
    assert saved["source_snapshot"]["related_tasks"][0]["status"] == "pending"
    during_draft.clear()
    now[0] += 26
    current = enrich()
    assert current["cached"] is False and len(calls) == 6
    assert current["cards"]["a:brief"]["reader_brief"]["next"] == "Goal edited while the call ran"


def test_unavailable_generation_keeps_stale_neighbor_snapshot_readable(cached_neighbors, monkeypatch):
    dataset, now, calls, _, saved, enrich = cached_neighbors
    dataset["tasks"][1]["status"] = "cancelled"
    now[0] += 26
    monkeypatch.setattr(map_narrative, "configured", lambda: False)
    result = enrich()
    assert result["available"] is False and result["cards"]["a:brief"] == saved and len(calls) == 2


@pytest.mark.parametrize("version", [None, 1])
def test_legacy_snapshot_is_not_backfilled_or_forced_to_generate(cached_neighbors, tmp_path, version):
    dataset, now, calls, _, _, enrich = cached_neighbors
    source = "live:neighbors:en-US"
    cache = map_narrative.read_cache(tmp_path, source)
    card = cache["cards"]["a:brief"]
    if version is None:
        card.pop("source_snapshot")
    else:
        card["source_snapshot"]["version"] = version
        card["source_snapshot"].pop("related_tasks")
    map_narrative._write_cache(map_narrative.cache_path(tmp_path, source), cache)
    dataset["tasks"][1]["objective"] = "A later neighboring goal"
    now[0] += 26
    result = enrich()
    assert result["cached"] is True and result["cards"]["a:brief"] == card and len(calls) == 2
    assert map_narrative.read_cache(tmp_path, source) == cache
