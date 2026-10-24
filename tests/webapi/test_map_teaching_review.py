"""Review transport/cache contracts; mocked verdicts do not certify teaching facts."""

import copy
import json
from unittest.mock import Mock

import pytest

from argus.webapi import map_teaching_review as teaching


def concept():
    return {
        "name": "Picard number",
        "explanation": "The Picard number is the rank of the Néron–Severi group.",
        "example": "If 18 curve classes are independent, the Picard number is exactly 18.",
        "connection": "The task is checking which hypotheses are available for a surface.",
    }


def accepted():
    return {"status": "accepted", "reason": "The definition and example follow the stated assumptions.",
            "findings": [], "replacement": None}


def correction():
    return {
        "status": "corrected", "reason": "The conclusion needs a bound and the vocabulary needs explaining.",
        "findings": [
            {"field": "example", "quote": "the Picard number is exactly 18",
             "kind": "unsupported_inference", "reason": "Independent classes give a lower bound, not an upper bound."},
            {"field": "explanation", "quote": "Néron–Severi group", "kind": "undefined_term",
             "reason": "This new specialist term is not explained for the beginner."},
        ],
        "replacement": {
            "name": "Picard number",
            "explanation": "It counts the number of independent algebraic curve-class directions on a surface; independent means no nonzero rational linear relation connects them.",
            "example": "Illustration: 18 independent curve classes show that the number is at least 18. To say exactly 18, one must also show that all the relevant classes lie in their span.",
            "connection": "This explains why finding classes and proving the exact count are different tasks.",
        },
    }


def invoke(candidates, run, **overrides):
    options = {
        "locale": "en-US", "context": {key: {"objective": "Check the stated hypotheses", "summary": "Work is ongoing", "source_ids": ["start-a"]} for key in candidates},
        "cached_reviews": {}, "model_revision": "model-a",
    }
    options.update(overrides)
    return teaching.review_concepts(candidates, run=run, **options)


    # The runner was mocked: this tests applying a correction, not detecting it.


def test_one_batch_deduplicates_exact_concepts_and_retains_null_without_review():
    other = {**concept(), "name": "Other concept"}
    run = Mock(return_value={"reviews": {"a": accepted(), "b": accepted()}})
    usable, receipts, updates = invoke({"a": concept(), "alias": concept(), "b": other, "none": None}, run)
    run.assert_called_once()
    _, schema = run.call_args.args
    assert set(schema["properties"]["reviews"]["properties"]) == {"a", "b"}
    assert usable["a"] == usable["alias"]
    assert receipts["a"]["input_revision"] == receipts["alias"]["input_revision"]
    assert usable["none"] is None and "none" not in receipts
    assert len(updates) == 2


def test_incomplete_cached_receipt_is_not_published_when_rechecking_fails():
    _, _, cache = invoke({"a": concept()}, Mock(return_value={"reviews": {"a": correction()}}))
    del next(iter(cache.values()))["reviewed_at"]
    usable, receipts, updates = invoke({"a": concept()}, Mock(side_effect=OSError("offline")), cached_reviews=cache)
    assert usable["a"] is None
    assert receipts["a"]["status"] == "unavailable" and updates == {}


def selected_source_context():
    return {
        "task": {
            "title": "Check the recorded conditions", "objective": "Determine whether the stated bound is exact",
            "summary": "The executor reported a lower bound", "status": "running",
            "acceptance_check": ["Check all hypotheses"],
            "outcome_source": {"status": "not_recorded_for_current_attempt", "attempt": 2},
        },
        "events": [{
            "id": f"source-{index}", "item_id": "task-a", "type": "round.review.completed", "revision": "revision-a",
            "ts": 100 + index, "text": f"Recorded comparison {index}", "status": "recorded",
            "next_action": f"Check condition {index} against the source", "review_skipped": False,
            "review_source": "engineer_self_review",
        } for index in range(9)],
    }


def test_source_projection_preserves_all_selected_ids_and_loss_flags_across_normalization_without_aliasing():
    source = selected_source_context()
    source.update(objective="Already clipped legacy context", objective_truncated=True,
                  source_ids=["stale-legacy-id"])
    source["task"].update(objective="o" * (teaching.TASK_SOURCE_LIMITS["objective"] + 1),
                          summary="An already clipped summary", summary_truncated=True,
                          non_goals=["No universal claim"], outcome={"review_status": "not_assessed"},
                          outcome_source={"event_id": "source-" + "z" * teaching.TASK_SOURCE_LIMITS["outcome_source"]})
    source["events"] = [{**source["events"][0], "id": f"source-{index}"}
                        for index in range(teaching.MAX_SOURCE_EVENTS + 2)]
    source["events"][0].update(text="An upstream excerpt", text_truncated=True,
                               next_action="n" * (teaching.EVENT_SOURCE_LIMITS["next_action"] + 1))
    before = copy.deepcopy(source)
    projected = teaching.teaching_context(source)
    expected_ids = [f"source-{index}" for index in range(teaching.MAX_SOURCE_EVENTS)]
    assert projected["source_ids"] == expected_ids
    assert len(expected_ids) > 6
    assert projected["events_truncated"] is True
    assert projected["objective_truncated"] is True
    assert projected["task"]["objective_truncated"] is True
    assert projected["task"]["summary_truncated"] is True
    assert projected["task"]["outcome_source_truncated"] is True
    assert projected["events"][0]["text_truncated"] is True
    assert projected["events"][0]["next_action_truncated"] is True
    assert projected["events"][0]["review_skipped"] is False

    again = teaching.teaching_context(projected)
    assert again == projected
    assert source == before
    snapshot = copy.deepcopy(projected)
    again["task"]["non_goals"].append("Local consumer edit")
    again["task"]["outcome"]["review_status"] = "local change"
    again["events"][0]["text"] = "Another local change"
    assert projected == snapshot and source == before


def test_related_task_projection_is_bounded_idempotent_and_excludes_unrelated_payloads():
    source = {"related_tasks": [
        {"id": f"task-{i}", "title": f"Neighbor {i}", "objective": "o" * 501,
         "deps": [f"dep-{j}" for j in range(teaching.MAX_RELATED_TASKS + 1)],
         "tool_output": "UNRELATED_TOOL_PAYLOAD", "source_snapshot": {"text": "old generated copy"}}
        for i in range(teaching.MAX_RELATED_TASKS + 2)
    ]}
    before = copy.deepcopy(source)
    projected = teaching.teaching_context(source)
    assert len(projected["related_tasks"]) == teaching.MAX_RELATED_TASKS
    assert projected["related_tasks_truncated"] is True
    assert projected["related_tasks"][0]["id"] == "task-0"
    assert projected["related_tasks"][0]["objective_truncated"] is True
    assert projected["related_tasks"][0]["deps_truncated"] is True
    assert "status" not in projected["related_tasks"][0]
    assert "UNRELATED_TOOL_PAYLOAD" not in json.dumps(projected)
    assert "old generated copy" not in json.dumps(projected)
    assert teaching.teaching_context(projected) == projected
    assert source == before


def test_changed_related_task_goal_invalidates_the_teaching_receipt():
    context = {"a": {**selected_source_context(), "related_tasks": [{
        "id": "f20a4421fc3f", "title": "Determine the exact prime set", "objective": "Test p=13", "deps": [],
    }]}}
    response = {"reviews": {"a": accepted()}, "readings": {"a": accepted()}}
    _, _, cache = invoke({"a": concept()}, Mock(return_value=copy.deepcopy(response)), context=context, reading={"a": reading()})
    context["a"]["related_tasks"][0]["objective"] = "Also determine the p=11 boundary"
    run = Mock(return_value=copy.deepcopy(response))
    _, _, updates = invoke({"a": concept()}, run, context=context, reading={"a": reading()}, cached_reviews=cache)
    run.assert_called_once()
    payload = json.loads(run.call_args.args[0].split("Teaching passages:\n", 1)[1])
    assert payload["related_tasks"] == context["a"]["related_tasks"]
    assert payload["passages"]["a"]["context"]["related_task_ids"] == ["f20a4421fc3f"]
    assert set(updates).isdisjoint(cache)


@pytest.mark.parametrize("error", [OSError("service unavailable"), TimeoutError("deadline"), ValueError("invalid JSON")])
def test_runner_failure_makes_no_retry_and_preserves_other_cached_reviews(error):
    _, _, cache = invoke({"cached": concept()}, Mock(return_value={"reviews": {"cached": accepted()}}))
    run = Mock(side_effect=error)
    usable, receipts, updates = invoke({"cached": concept(), "new": {**concept(), "name": "New concept"}}, run, cached_reviews=cache)
    run.assert_called_once()
    assert usable["cached"] == concept() and usable["new"] is None
    assert receipts["new"]["error_code"] == "review_failed" and updates == {}


@pytest.mark.parametrize("defect", ["invented_quote", "accepted_with_findings", "partial_replacement", "unchanged_replacement"])
def test_invalid_correction_contract_cannot_publish_a_candidate(defect):
    verdict = correction()
    if defect == "invented_quote":
        verdict["findings"][0]["quote"] = "This sentence was never in the example"
    elif defect == "accepted_with_findings":
        verdict["status"] = "accepted"
        verdict["replacement"] = None
    elif defect == "partial_replacement":
        del verdict["replacement"]["example"]
    else:
        verdict["replacement"] = concept()
    usable, receipts, updates = invoke({"a": concept()}, Mock(return_value={"reviews": {"a": verdict}}))
    assert usable["a"] is None and receipts["a"]["status"] == "unavailable"
    assert "error_code" in receipts["a"] and updates == {}


def test_batch_limit_does_not_start_another_model_call():
    candidates = {str(i): {**concept(), "name": f"Concept {i}"} for i in range(teaching.MAX_REVIEW_CARDS + 1)}
    run = Mock(return_value={"reviews": {str(i): accepted() for i in range(teaching.MAX_REVIEW_CARDS)}})
    usable, receipts, _ = invoke(candidates, run)
    run.assert_called_once()
    extra = str(teaching.MAX_REVIEW_CARDS)
    assert usable[extra] is None and receipts[extra]["error_code"] == "review_batch_limit"


def reading():
    return {
        "title": "Check the isometry criterion for the new objects",
        "why": "The isometry criterion might let the existing method cover a new class.",
        "scope": "Only the task start is recorded; no current proof or independent review is recorded.",
        "next": "The recorded plan is to check the source conditions and write the result or obstruction.",
        "summary": "Check the isometry criterion for one new object.",
        "detail": "The task asks whether the criterion extends the earlier construction to pairs of objects, with complete reasoning or an obstruction tied to the source conditions.",
    }


def reading_correction():
    return {
        "status": "corrected", "reason": "Explain the purpose before using a specialist criterion name.",
        "findings": [{"field": "title", "quote": "isometry criterion", "kind": "undefined_term",
                      "reason": "The reader needs to know what is being checked without knowing this term."}],
        "replacement": {
            "title": "Check whether the earlier method applies to these new objects",
            "why": "The task asks whether a method already used for one class of objects could work for another. This remains a possibility to check.",
            "scope": "The assignment concerns pairs of the new objects and requires complete reasoning or a source-based obstruction; no result has yet been recorded.",
            "next": "The recorded assignment is to check the source conditions and explain why the extension works or what blocks it.",
            "summary": "Check whether the earlier construction extends to pairs of the new objects.",
            "detail": "For pairs of the new objects, check every hypothesis of the source criterion. The recorded assignment requires complete reasoning for the extension or an obstruction analysis tied to those source conditions; it does not report a completed extension.",
        },
    }


def unavailable_decision():
    return {"status": "unavailable", "reason": "A faithful explanation cannot be confirmed from these records.",
            "findings": [], "replacement": None}


@pytest.mark.parametrize("field", ["title", "why", "scope", "next", "summary", "detail"])
def test_reading_replacement_needing_redaction_cannot_publish_a_partial_correction(redact_secrets_on, field):
    draft = reading()
    before = copy.deepcopy(draft)
    decision = reading_correction()
    decision["replacement"][field] = "api_key=synthetic-review-credential-0000"
    response = {"reviews": {"a": accepted()}, "readings": {"a": decision}}
    response_before = copy.deepcopy(response)
    run = Mock(return_value=response)
    usable, receipts, updates = invoke({"a": concept()}, run, reading={"a": draft})
    run.assert_called_once()
    assert usable["a"] == concept() and receipts["a"]["status"] == "accepted"
    assert receipts["a"]["reading_review"]["status"] == "unavailable"
    assert receipts["a"]["reading_review"]["error_code"] == "replacement_requires_redaction"
    assert "reading_replacement" not in receipts["a"] and updates == {}
    assert draft == before and response == response_before


def test_cached_reading_replacement_is_reused_without_a_call_or_mutating_the_cache():
    response = {"reviews": {}, "readings": {"a": reading_correction()}}
    _, _, cache = invoke({"a": None}, Mock(return_value=response), reading={"a": reading()})
    before = copy.deepcopy(cache)
    run = Mock(side_effect=AssertionError("Identical reading was already checked"))
    _, receipts, updates = invoke({"a": None}, run, reading={"a": reading()}, cached_reviews=cache)
    run.assert_not_called()
    assert receipts["a"]["reading_replacement"] == reading_correction()["replacement"]
    assert updates == {}
    receipts["a"]["reading_replacement"]["title"] = "Local mutation"
    receipts["a"]["reading_review"]["findings"].clear()
    assert cache == before


def test_unavailable_reading_does_not_remove_a_separately_corrected_concept():
    draft = reading()
    run = Mock(return_value={"reviews": {"a": correction()}, "readings": {"a": unavailable_decision()}})
    usable, receipts, updates = invoke({"a": concept()}, run, reading={"a": draft})
    assert usable["a"] == correction()["replacement"]
    assert receipts["a"]["status"] == "corrected"
    assert receipts["a"]["reading_review"]["status"] == "unavailable"
    assert "reading_replacement" not in receipts["a"]
    assert draft == reading() and len(updates) == 1


@pytest.mark.parametrize("field", ["title", "why", "scope", "next", "summary", "detail"])
@pytest.mark.parametrize("defect", ["oversized", "missing"])
def test_reading_candidates_are_not_truncated_or_silently_missing_fields(field, defect):
    draft = reading()
    if defect == "oversized":
        draft[field] = "x" * (teaching.READING_LIMITS[field] + 1)
    else:
        del draft[field]
    before = copy.deepcopy(draft)
    run = Mock(side_effect=AssertionError("Invalid reading cannot be reviewed"))
    _, receipts, updates = invoke({"a": None}, run, reading={"a": draft})
    assert receipts["a"]["reading_review"]["error_code"] == "invalid_reading"
    assert "reading_replacement" not in receipts["a"] and updates == {}
    assert draft == before
    run.assert_not_called()
