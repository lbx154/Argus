"""End-to-end presentation plumbing; supplied model verdicts are not fact checks."""
import copy
import json
from dataclasses import replace

import pytest

from argus_skill.webapi import map_narrative
from argus_skill.webapi import map_teaching_review as teaching
from argus_skill.webapi.map_model import MapModel


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


def reading_fields(value):
    return {field: value[field] for field in ("title", "summary", "detail")} | {
        field: value["reader_brief"][field] for field in ("why", "scope", "next")
    }


def capture_generation_sources(monkeypatch, documents, tasks=None):
    """Exercise both transports with supplied verdicts, never a model endpoint."""
    observed = {}
    calls = []

    def run(prompt, schema, _config, **_kwargs):
        calls.append(schema)
        if "cards" in schema["properties"]:
            observed["draft"] = json.loads(prompt.split("\n研究记录：\n", 1)[1])
            return {"cards": {doc["key"]: copy.deepcopy(card()) for doc in documents}, "relations": []}
        observed["checker_payload"] = json.loads(prompt.split("Teaching passages:\n", 1)[1])
        observed["checker"] = copy.deepcopy(observed["checker_payload"]["passages"])
        related = {task["id"]: task for task in observed["checker_payload"]["related_tasks"]}
        for row in observed["checker"].values():
            context = row["context"]
            if "related_task_ids" in context:
                context["related_tasks"] = [copy.deepcopy(related[task_id]) for task_id in context.pop("related_task_ids")]
        verdict = {"status": "accepted", "reason": "Supplied transport-test verdict", "findings": [], "replacement": None}
        return {section: {key: copy.deepcopy(verdict) for key in schema["properties"][section]["properties"]}
                for section in ("reviews", "readings")}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    observed["result"] = map_narrative.generate(documents, tasks if tasks is not None else [{"id": doc["task_id"]} for doc in documents], "en-US",
                                                config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"), project_root=None, global_root=None)
    assert len(calls) == 2
    return observed


def test_draft_and_checker_share_actual_task_evidence_and_attribution_without_mutating_sources(monkeypatch):
    documents = []
    for key in ("a", "b"):
        source = document()
        source.update(key=key, task_id=key, kind="task")
        source["task"].update(
            title=f"Recorded task {key}", summary=f"Executor reported only a lower bound for {key}",
            status="running", acceptance_check=["Check all stated conditions", "Preserve the scope limit"],
            plan_hypothesis="An exact count remains a hypothesis", goal_contribution="Determine the scope",
            outcome={"execution_status": "running", "review_status": "not_assessed"},
            outcome_source={"status": "not_recorded_for_current_attempt", "attempt": 2},
            attempt=2, started_ts=100.0, tool_output="UNRELATED_TASK_OUTPUT" * 1000,
        )
        source["events"] = [{
            "id": f"{key}-source-{index}", "item_id": key, "type": "round.main.completed", "ts": 101 + index,
            "text": f"The executor recorded comparison {index} for {key}",
            "next_action": f"For {key}, inspect the explicit condition {index} before drawing a conclusion.",
            "tool_output": "UNRELATED_EVENT_OUTPUT" * 1000, "cursor": "tool-tick",
        } for index in range(9)]
        source["events"][-1].update(review_skipped=False, review_source="engineer_self_review",
                                    success=False, overall_complete=False, campaign_continues=True)
        documents.append(source)
    before = copy.deepcopy(documents)
    observed = capture_generation_sources(monkeypatch, documents)

    for sent in observed["draft"]["cards"]:
        key = sent["key"]
        original = next(doc for doc in before if doc["key"] == key)
        checked = observed["checker"][key]["context"]
        assert observed["checker"][key]["reading"] == reading_fields(card())
        snapshot = next(row for row in observed["result"]["cards"] if row["key"] == key)["source_snapshot"]
        assert snapshot["version"] == map_narrative.SOURCE_SNAPSHOT_VERSION and snapshot["card_key"] == key and snapshot["task_id"] == original["task_id"]
        assert {field: value for field, value in snapshot.items()
                if field not in {"version", "card_key", "task_id", "captured_at"}} == checked
        assert checked["task"] == sent["task"]
        assert checked["events"] == sent["events"]
        assert checked["source_ids"] == sent["source_ids"] == [event["id"] for event in original["events"]]
        assert len(checked["source_ids"]) == 9
        for field in ("summary", "acceptance_check", "outcome", "outcome_source", "plan_hypothesis", "non_goals"):
            assert checked["task"][field] == original["task"][field]
        assert "summary" not in checked  # No title/non-goals surrogate for a research summary.
        assert checked["events"][-1]["review_skipped"] is False
        assert checked["events"][-1]["success"] is False
        assert checked["events"][-1]["overall_complete"] is False
        assert checked["events"][-1]["review_source"] == "engineer_self_review"
        assert checked["events"][-1]["next_action"] == original["events"][-1]["next_action"]
        assert all(event["item_id"] == key for event in checked["events"])
        assert "success" not in checked["events"][0]
        encoded = json.dumps({"draft": sent, "checker": checked})
        assert "UNRELATED_TASK_OUTPUT" not in encoded and "UNRELATED_EVENT_OUTPUT" not in encoded
        assert "tool-tick" not in encoded
        assert card()["summary"] not in json.dumps(checked)
    assert documents == before


def test_upstream_evidence_truncation_remains_visible_to_both_draft_and_checker(monkeypatch):
    dataset = {"tasks": [{"id": "a", "title": "Task", "status": "running",
                          "objective": "o" * (teaching.TASK_SOURCE_LIMITS["objective"] + 1)}],
               "events": [{"id": "source-a", "item_id": "a", "type": "round.main.completed", "ts": 1,
                           "text": "e" * 3000, "next_action": "n" * 1800, "review_skipped": False}]}
    before = copy.deepcopy(dataset)
    documents = map_narrative.card_evidence(dataset, [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["source-a"]}])
    assert documents[0]["events"][0]["next_action_truncated"] is True
    document_snapshot = copy.deepcopy(documents)
    observed = capture_generation_sources(monkeypatch, documents)
    draft = observed["draft"]["cards"][0]
    checked = observed["checker"]["a"]["context"]
    snapshot = observed["result"]["cards"][0]["source_snapshot"]
    assert snapshot["task"] == checked["task"] and snapshot["events"] == checked["events"]
    assert draft["task"] == checked["task"]
    assert draft["events"] == checked["events"]
    assert checked["task"]["objective_truncated"] is True
    assert checked["events"][0]["text_truncated"] is True
    # This value already fits the second projector: its upstream loss flag must survive.
    assert len(checked["events"][0]["next_action"]) == 1500
    assert checked["events"][0]["next_action_truncated"] is True
    assert checked["events"][0]["review_skipped"] is False
    assert dataset == before and documents == document_snapshot


def test_bsd_neighbor_goals_are_shared_once_with_checker_and_retained_by_id(monkeypatch):
    # Offline source fixture reproducing the two neighboring goals which the
    # earlier draft saw but its checker and saved evidence did not receive.
    source = document()
    source.update(key="1e8d7b0d1acf", task_id="1e8d7b0d1acf")
    other = document()
    other.update(key="another-card", task_id="another-task")
    neighbors = [
        {"id": "f20a4421fc3f", "title": "Extend the multiplicative irreducible Serre-weight obstruction to its exact prime set",
         "objective": "Determine coverage at p=13 as well as p=5,7 and why the same argument stops at p=11 and p>=17.", "deps": []},
        {"id": "ba728f561897", "title": "Test the multiplicative p=3 branch of semistable rank-one BSD",
         "objective": "Determine the 3-part formula, distinguishing irreducible and reducible residual representations and split versus nonsplit reduction.", "deps": []},
    ]
    tasks = [{"id": source["task_id"]}, {"id": other["task_id"]}, *neighbors]
    before = copy.deepcopy(tasks)
    observed = capture_generation_sources(monkeypatch, [source, other], tasks)
    draft, checker = observed["draft"], observed["checker_payload"]
    assert "tasks" not in draft  # No extra long source available only to the draft.
    draft_table = {task["id"]: task for task in draft["related_tasks"]}
    checker_table = {task["id"]: task for task in checker["related_tasks"]}
    assert draft_table == checker_table
    for neighbor in neighbors:
        assert checker_table[neighbor["id"]] == neighbor
        assert "status" not in checker_table[neighbor["id"]]  # Missing state stays unknown.
        assert json.dumps(draft).count(neighbor["objective"]) == 1
        assert json.dumps(checker).count(neighbor["objective"]) == 1
    for sent in draft["cards"]:
        checked = checker["passages"][sent["key"]]["context"]
        assert checked["related_task_ids"] == sent["related_task_ids"]
        snapshot = next(row["source_snapshot"] for row in observed["result"]["cards"] if row["key"] == sent["key"])
        assert snapshot["related_tasks"] == [draft_table[key] for key in sent["related_task_ids"]]
        assert {neighbor["id"] for neighbor in neighbors} <= set(sent["related_task_ids"])
        assert "related_task_ids" not in snapshot  # Saved evidence is self-contained.
    assert tasks == before
    neighbors[0]["objective"] = "A later edited goal"
    assert checker_table["f20a4421fc3f"]["objective"] != neighbors[0]["objective"]


def test_source_snapshot_binds_the_pre_generation_material_when_live_task_and_events_change(monkeypatch):
    source = document()
    source.update(key="start-a", kind="execution")
    source["events"][0].update(item_id="a", revision="event-before", type="life.mission.started", ts=900)
    before = copy.deepcopy(source)
    now = {"value": 1000.0}
    observed = {}
    monkeypatch.setattr(map_narrative.time, "time", lambda: now["value"])

    def run(prompt, schema, _config, **_kwargs):
        if "cards" in schema["properties"]:
            observed["draft"] = json.loads(prompt.split("\n研究记录：\n", 1)[1])["cards"][0]
            assert "source_snapshot" not in schema["properties"]["cards"]["properties"]["start-a"]["properties"]
            # Simulate progress arriving while the first model call is running.
            source["task"]["objective"] = "A revised task objective"
            source["events"][0].update(text="An updated record", revision="event-after")
            source["events"].append({"id": "completed-a", "item_id": "a", "type": "round.main.completed", "text": "Later progress"})
            now["value"] = 1100.0
            return {"cards": {"start-a": copy.deepcopy(card())}, "relations": []}
        observed["checker"] = json.loads(prompt.split("Teaching passages:\n", 1)[1])["passages"]["start-a"]["context"]
        now["value"] = 1200.0
        verdict = {"status": "accepted", "reason": "Supplied transport-test verdict", "findings": [], "replacement": None}
        return {"reviews": {"start-a": copy.deepcopy(verdict)}, "readings": {"start-a": copy.deepcopy(verdict)}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.generate([source], [{"id": "a"}], "en-US",
                                   config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"), project_root=None, global_root=None)
    snapshot = result["cards"][0]["source_snapshot"]
    assert snapshot["card_key"] == "start-a" and snapshot["task_id"] == "a"
    assert snapshot["captured_at"] == 1000.0 < now["value"]
    assert snapshot["task"] == observed["draft"]["task"] == observed["checker"]["task"]
    assert snapshot["events"] == observed["draft"]["events"] == observed["checker"]["events"]
    assert snapshot["task"]["objective"] == before["task"]["objective"]
    assert snapshot["events"][0]["revision"] == "event-before"
    assert snapshot["source_ids"] == ["start-a"]
    assert source["events"][-1]["id"] == "completed-a"
    snapshot["task"]["non_goals"].append("A consumer-local edit")
    assert source["task"]["non_goals"] == before["task"]["non_goals"]


def test_snapshot_persists_with_its_card_and_cached_or_coalesced_reads_never_backfill_it(tmp_path, monkeypatch):
    calls = []
    phases = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: MapModel("pi", "gpt-5.5", "medium", "argus-pi"))
    monkeypatch.setattr(map_narrative.time, "time", lambda: 1000.0)

    def run(_prompt, schema, _config, **_kwargs):
        if _kwargs.get("on_progress") is not None:
            _kwargs["on_progress"](_kwargs["phase"])
        calls.append(schema)
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(card())}, "relations": []}
        verdict = {"status": "accepted", "reason": "Supplied transport-test verdict", "findings": [], "replacement": None}
        return {"reviews": {"a": copy.deepcopy(verdict)}, "readings": {"a": copy.deepcopy(verdict)}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    dataset = {"id": "live:snapshots", "tasks": [{"id": "a", "title": "Task", "objective": "The initial goal",
                                                  "status": "running", "revision": "task-before", "deps": []},
                                                 {"id": "f20a4421fc3f", "title": "Exact prime set", "objective": "Test p=13", "status": "pending", "deps": []},
                                                 {"id": "ba728f561897", "title": "Multiplicative p=3 branch", "objective": "Determine the 3-part formula", "status": "done", "deps": []}],
               "events": [{"id": "start-a", "item_id": "a", "type": "life.mission.started", "ts": 900,
                           "text": "The initial record", "revision": "event-before"}]}
    before = copy.deepcopy(dataset)
    request = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["start-a"]}]
    first = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path, on_progress=phases.append)
    saved = copy.deepcopy(first["cards"]["a"])
    snapshot = saved["source_snapshot"]
    assert len(calls) == 2 and snapshot["captured_at"] == 1000.0
    assert phases == ["waiting_for_source", "writing", "reviewing"]
    assert snapshot["task"]["objective"] == "The initial goal" and snapshot["source_ids"] == ["start-a"]
    assert snapshot["related_tasks"] == dataset["tasks"][1:]
    assert saved["task_revision"] == "task-before" and saved["event_revisions"] == ["event-before"]
    cache_source = "live:snapshots:en-US"
    assert map_narrative.read_cache(tmp_path, cache_source)["cards"]["a"]["source_snapshot"] == snapshot
    phases.clear()
    again = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path, on_progress=phases.append)
    assert again["cached"] is True and again["cards"]["a"] == saved and len(calls) == 2
    assert phases == ["waiting_for_source"]

    later = copy.deepcopy(dataset)
    later["tasks"][0].update(objective="A changed goal", revision="task-after")
    later["tasks"][1].update(objective="A later neighboring goal", status="done")
    later["events"].append({"id": "completed-a", "item_id": "a", "type": "round.main.completed", "ts": 950,
                            "text": "New progress", "revision": "new-event"})
    later_request = [{**request[0], "event_ids": ["start-a", "completed-a"]}]
    phases.clear()
    coalesced = map_narrative.enrich(tmp_path, later, later_request, "en-US", project_root=tmp_path, on_progress=phases.append)
    assert coalesced["retry_after"] == 25 and coalesced["cards"]["a"] == saved
    assert len(calls) == 2 and dataset == before
    assert phases == ["waiting_for_source"]

    # An otherwise current pre-snapshot cache remains readable without a new call.
    legacy = map_narrative.read_cache(tmp_path, cache_source)
    legacy["cards"]["a"].pop("source_snapshot")
    map_narrative._write_cache(map_narrative.cache_path(tmp_path, cache_source), legacy)
    legacy_before = copy.deepcopy(legacy)
    cached = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert cached["cached"] is True and "source_snapshot" not in cached["cards"]["a"]
    assert len(calls) == 2 and map_narrative.read_cache(tmp_path, cache_source) == legacy_before


def test_one_draft_and_one_check_share_a_deadline_and_a_cached_check_is_reused(monkeypatch):
    observed = []
    phases = []
    original = card()
    replacement = {**original["reader_brief"]["concept"],
                   "example": "Two independent directions give a lower bound of two; an exact count needs a spanning argument"}
    reading = {**reading_fields(original), "title": "Check what the evidence can tell us",
               "summary": "The reported count remains a lower bound until the spanning condition is checked.",
               "detail": "Independence gives a lower bound. An exact count also requires spanning the whole space.",
               "scope": "The reported result is a lower bound, not an exact count."}

    def run(prompt, schema, _config, **kwargs):
        kwargs["on_progress"](kwargs["phase"])
        observed.append((prompt, kwargs["deadline"], _config))
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(original)}, "relations": []}
        return {"reviews": {"a": {
            "status": "corrected", "reason": "The exact count needs more evidence", "replacement": replacement,
            "findings": [{"field": "example", "quote": "exactly two", "kind": "unsupported_inference",
                          "reason": "Independence only establishes a lower bound"}],
        }}, "readings": {"a": {
            "status": "corrected", "reason": "Describe the action in ordinary words", "replacement": reading,
            "findings": [{"field": "title", "quote": original["title"], "kind": "undefined_term",
                          "reason": "Explain what is being checked"}],
        }}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    monkeypatch.setattr(map_narrative.time, "monotonic", lambda: 1000.0)
    config = MapModel("pi", "gpt-5.5", "medium", "argus-pi", review_effort="high")
    kwargs = {"config": config, "project_root": None, "global_root": None, "on_progress": phases.append}
    result = map_narrative.generate([document()], [{"id": "a"}], "en-US", **kwargs)
    assert len(observed) == 2 and observed[0][1] == observed[1][1]
    assert all(prompt.count(teaching.TEACHING_CORE) == 1 for prompt, _, _ in observed)
    assert phases == ["writing", "reviewing"]
    assert observed[0][1] == 1170.0
    assert observed[0][2].effort == "medium" and observed[1][2].effort == "high"
    saved = result["cards"][0]
    assert saved["reader_brief"]["concept"] == replacement
    assert saved["teaching_review"]["status"] == "corrected"
    assert reading_fields(saved) == reading
    assert saved["teaching_review"]["reading_review"]["status"] == "corrected"
    assert "reading_replacement" not in saved["teaching_review"]
    assert result["teaching_reviews"]
    again = map_narrative.generate([document()], [{"id": "a"}], "en-US",
                                   cached_reviews=result["teaching_reviews"], **kwargs)
    assert len(observed) == 3  # A new draft, with no repeated concept review.
    assert phases == ["writing", "reviewing", "writing"]
    assert again["cards"][0]["reader_brief"]["concept"] == replacement
    assert reading_fields(again["cards"][0]) == reading
    # Same source and draft, but a different checker must not reuse its receipt.
    kwargs["config"] = replace(config, review_effort="medium")
    map_narrative.generate([document()], [{"id": "a"}], "en-US",
                           cached_reviews=result["teaching_reviews"], **kwargs)
    assert len(observed) == 5 and observed[-1][2].effort == "medium"
    assert phases == ["writing", "reviewing", "writing", "writing", "reviewing"]


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
                                   config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"), project_root=None, global_root=None)
    assert calls == 2
    saved = result["cards"][0]
    assert saved["reader_brief"]["concept"] is None
    assert reading_fields(saved) == reading_fields(original)
    assert saved["teaching_review"]["status"] == "unavailable"
    assert saved["teaching_review"]["reading_review"]["status"] == "unavailable"
    assert result["teaching_reviews"] == {}


@pytest.mark.parametrize("defect", ["missing-detail", "redaction-required"])
def test_unusable_reading_replacement_never_partially_applies_scope_or_card_text(monkeypatch, defect):
    original = card()
    replacement = {**reading_fields(original), "title": "Replacement title", "summary": "Replacement summary",
                   "scope": "Replacement scope", "detail": "Replacement precise conditions"}
    if defect == "missing-detail":
        replacement.pop("detail")
    else:
        replacement["detail"] = "A generated credential: " + "ghp_" + "A" * 36
    calls = []

    def run(_prompt, schema, _config, **_kwargs):
        calls.append(schema)
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(original)}, "relations": []}
        return {"reviews": {"a": {"status": "accepted", "reason": "Supplied transport verdict",
                                   "findings": [], "replacement": None}},
                "readings": {"a": {"status": "corrected", "reason": "Supplied correction",
                                    "findings": [{"field": "scope", "quote": original["reader_brief"]["scope"],
                                                  "kind": "changed_meaning", "reason": "Preserve the actual boundary"}],
                                    "replacement": replacement}}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.generate([document()], [{"id": "a"}], "en-US",
                                   config=MapModel("pi", "gpt-5.5", "medium", "argus-pi"), project_root=None, global_root=None)
    saved = result["cards"][0]
    assert len(calls) == 2
    assert reading_fields(saved) == reading_fields(original)
    assert saved["teaching_review"]["reading_review"]["status"] == "unavailable"
    assert "reading_replacement" not in saved["teaching_review"]
    assert result["teaching_reviews"] == {}


def configured_enrichment(monkeypatch):
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: MapModel("pi", "gpt-5.5", "medium", "argus-pi"))
    dataset = {"id": "live:checked-text", "tasks": [{"id": "a", "title": "Task", "objective": "Original objective",
                                                   "status": "running", "revision": "task-v1", "deps": []}],
               "events": []}
    request = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": []}]
    return dataset, request


def test_corrected_detail_keeps_its_final_condition_in_the_same_persisted_card(tmp_path, monkeypatch):
    dataset, request = configured_enrichment(monkeypatch)
    original = card()
    final_condition = "\nThe conclusion applies only when the selected objects span the whole space."
    detail = "D" * (4000 - len(final_condition)) + final_condition
    replacement = {**reading_fields(original), "scope": "The claim is conditional on spanning the whole space.",
                   "summary": "The record claims an exact count only under the spanning condition.", "detail": detail}
    calls = []

    def run(prompt, schema, _config, **_kwargs):
        calls.append(schema)
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(original)}, "relations": []}
        candidate = json.loads(prompt.split("Teaching passages:\n", 1)[1])["passages"]["a"]["reading"]
        assert candidate == reading_fields(original)
        return {"reviews": {"a": {"status": "accepted", "reason": "Supplied transport verdict",
                                   "findings": [], "replacement": None}},
                "readings": {"a": {"status": "corrected", "reason": "Keep the formal condition with the reading",
                                    "findings": [{"field": "detail", "quote": original["detail"],
                                                  "kind": "changed_meaning", "reason": "The final condition is decisive"}],
                                    "replacement": replacement}}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    saved = result["cards"]["a"]
    persisted = map_narrative.read_cache(tmp_path, "live:checked-text:en-US")["cards"]["a"]
    assert len(calls) == 2 and reading_fields(saved) == replacement
    assert persisted == saved and persisted["detail"].endswith(final_condition)
    assert len(persisted["detail"]) == 4000
    assert persisted["teaching_review"]["reading_review"]["status"] == "corrected"
    assert set(persisted["reader_brief"]) == {"why", "scope", "next", "concept"}


@pytest.mark.parametrize("field,limit", [("summary", 250), ("detail", 4000)])
def test_oversize_generated_text_is_rejected_without_replacing_cached_conditions(tmp_path, monkeypatch, field, limit):
    dataset, request = configured_enrichment(monkeypatch)
    now = {"value": 1000.0}
    monkeypatch.setattr(map_narrative.time, "time", lambda: now["value"])
    generated = card()
    calls = []

    def run(_prompt, schema, _config, **_kwargs):
        calls.append(schema)
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(generated)}, "relations": []}
        verdict = {"status": "accepted", "reason": "Supplied transport verdict", "findings": [], "replacement": None}
        return {"reviews": {"a": copy.deepcopy(verdict)}, "readings": {"a": copy.deepcopy(verdict)}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    previous = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)["cards"]["a"]
    generated[field] = "x" * limit + "!"
    dataset["tasks"][0].update(objective="A later objective", revision="task-v2")
    now["value"] += 30
    with pytest.raises(ValueError, match="invalid card copy"):
        map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert len(calls) == 3  # The invalid draft never enters a second checking call.
    cached = map_narrative.read_cache(tmp_path, "live:checked-text:en-US")
    assert cached["cards"]["a"] == previous


def test_secret_redaction_precedes_checking_and_keeps_the_same_conditions_in_storage(tmp_path, monkeypatch):
    dataset, request = configured_enrichment(monkeypatch)
    generated = card()
    fake_token = "ghp_" + "A" * 36
    generated["detail"] = f"Credential {fake_token}\nThe result requires both stated conditions."
    expected = "Credential <REDACTED:github-token>\nThe result requires both stated conditions."
    checked = []

    def run(prompt, schema, _config, **_kwargs):
        if "cards" in schema["properties"]:
            return {"cards": {"a": copy.deepcopy(generated)}, "relations": []}
        candidate = json.loads(prompt.split("Teaching passages:\n", 1)[1])["passages"]["a"]["reading"]
        checked.append(candidate)
        assert candidate["detail"] == expected and fake_token not in prompt
        verdict = {"status": "accepted", "reason": "Supplied transport verdict", "findings": [], "replacement": None}
        return {"reviews": {"a": copy.deepcopy(verdict)}, "readings": {"a": copy.deepcopy(verdict)}}

    monkeypatch.setattr(map_narrative, "run_map_model", run)
    result = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    saved = result["cards"]["a"]
    assert len(checked) == 1 and reading_fields(saved) == checked[0]
    persisted = map_narrative.read_cache(tmp_path, "live:checked-text:en-US")
    assert persisted["cards"]["a"]["detail"] == expected
    assert fake_token not in json.dumps(persisted)


def test_redaction_that_expands_scope_past_its_limit_cannot_clip_the_final_condition():
    original = card()["reader_brief"]
    prefix, tail = "api_key=12345678; ", "Only if n > 0."
    original["scope"] = prefix + "x" * (700 - len(prefix) - len(tail)) + tail
    before = copy.deepcopy(original)
    assert len(original["scope"]) == 700
    with pytest.raises(ValueError, match="invalid reader brief text"):
        map_narrative._reader_brief(original)
    assert original == before and original["scope"].endswith(tail)


def test_narration_context_keeps_current_cards_and_direct_dependencies_without_unrelated_history():
    tasks = [{"id": str(index), "deps": []} for index in range(100)]
    tasks[90]["deps"] = ["3", "7"]
    tasks[3]["deps"] = ["2"]
    before = copy.deepcopy(tasks)
    selected = map_narrative.generation_context_tasks(tasks, [{"task_id": "90"}, {"task_id": "91"}])
    assert [task["id"] for task in selected] == ["90", "91", "3", "7"]
    assert tasks == before


@pytest.mark.parametrize("with_concept", [True, False])
@pytest.mark.parametrize("key", ["a", "a:brief"])
def test_cached_card_does_not_bypass_a_new_model_or_teaching_checker(tmp_path, monkeypatch, with_concept, key):
    config = MapModel("pi", "gpt-5.5", "medium", "argus-pi")
    calls = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: config)

    def generate(documents, *_args, **_kwargs):
        calls.append(documents)
        value = card()
        if not with_concept:
            value["reader_brief"]["concept"] = None
        return {"cards": [{**value, "key": document["key"], "teaching_review": {
            "status": "accepted", "review_version": map_narrative.TEACHING_REVIEW_VERSION,
        }} for document in documents], "relations": []}

    monkeypatch.setattr(map_narrative, "generate", generate)
    dataset = {"id": "live:cache", "tasks": [{"id": "a", "title": "Task", "status": "pending", "deps": []}], "events": []}
    request = [{"key": key, "task_id": "a", "kind": "task", "event_ids": []}]
    first = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)["cached"]
    assert len(calls) == 1
    config = replace(config, review_effort="high")
    second = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert len(calls) == 2 and second["cards"][key]["model_revision"] == config.revision
    monkeypatch.setattr(map_narrative, "TEACHING_REVIEW_VERSION", 99)
    third = map_narrative.enrich(tmp_path, dataset, request, "en-US", project_root=tmp_path)
    assert len(calls) == 3
    assert first["cards"][key]["input_revision"] != third["cards"][key]["input_revision"]


def test_later_focused_cards_can_add_relationships_for_their_own_context(tmp_path, monkeypatch):
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: MapModel("pi", "gpt-5.5", "medium", "argus-pi"))
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
