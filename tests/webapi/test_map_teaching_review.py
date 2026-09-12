"""Review transport/cache contracts; mocked verdicts do not certify teaching facts."""

import copy
import json
from unittest.mock import Mock

import pytest

from argus_skill.webapi import map_teaching_review as teaching


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


def test_applies_supplied_two_issue_verdict_without_publishing_the_bad_example():
    original = concept()
    verdict = correction()
    run = Mock(return_value={"reviews": {"a": verdict}})
    usable, receipts, updates = invoke({"a": original}, run)
    assert usable["a"] == verdict["replacement"]
    assert usable["a"] != original
    assert receipts["a"]["status"] == "corrected"
    assert [item["kind"] for item in receipts["a"]["findings"]] == ["unsupported_inference", "undefined_term"]
    assert receipts["a"]["kind"] == "model_teaching_review"
    assert "review_status" not in receipts["a"] and "stage_certification" not in receipts["a"]
    assert len(updates) == 1
    run.assert_called_once()
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


def test_only_null_concepts_need_no_call():
    run = Mock(side_effect=AssertionError("No teaching text to review"))
    assert invoke({"a": None}, run) == ({"a": None}, {}, {})
    run.assert_not_called()


def test_cached_correction_is_reused_and_input_objects_remain_independent():
    candidates = {"a": concept()}
    original = copy.deepcopy(candidates)
    _, _, cache = invoke(candidates, Mock(return_value={"reviews": {"a": correction()}}))
    snapshot = copy.deepcopy(cache)
    run = Mock(side_effect=AssertionError("Cache hit must not invoke a model"))
    usable, receipts, updates = invoke(candidates, run, cached_reviews=cache)
    assert receipts["a"]["status"] == "corrected" and updates == {}
    usable["a"]["example"] = "A local consumer edited its copy"
    receipts["a"]["findings"].clear()
    assert candidates == original and cache == snapshot
    run.assert_not_called()


def test_incomplete_cached_receipt_is_not_published_when_rechecking_fails():
    _, _, cache = invoke({"a": concept()}, Mock(return_value={"reviews": {"a": correction()}}))
    del next(iter(cache.values()))["reviewed_at"]
    usable, receipts, updates = invoke({"a": concept()}, Mock(side_effect=OSError("offline")), cached_reviews=cache)
    assert usable["a"] is None
    assert receipts["a"]["status"] == "unavailable" and updates == {}


@pytest.mark.parametrize("change", ["candidate", "context", "locale", "model", "review_version"])
def test_review_cache_invalidates_when_the_actual_review_input_changes(monkeypatch, change):
    candidates = {"a": concept()}
    _, _, cache = invoke(candidates, Mock(return_value={"reviews": {"a": accepted()}}))
    overrides = {"cached_reviews": cache}
    if change == "candidate":
        candidates["a"]["connection"] += " A new connection."
    elif change == "context":
        overrides["context"] = {"a": {"objective": "Check a different hypothesis"}}
    elif change == "locale":
        overrides["locale"] = "zh-CN"
    elif change == "model":
        overrides["model_revision"] = "model-b"
    else:
        monkeypatch.setattr(teaching, "TEACHING_REVIEW_VERSION", teaching.TEACHING_REVIEW_VERSION + 1)
    run = Mock(return_value={"reviews": {"a": accepted()}})
    _, _, updates = invoke(candidates, run, **overrides)
    run.assert_called_once()
    assert set(updates).isdisjoint(cache)


def test_only_bounded_context_is_sent_and_tool_ticks_do_not_invalidate_review():
    context = {"a": {"objective": "o" * 1000, "summary": "s" * 2000,
                     "source_ids": [f"source-{i}" for i in range(100)],
                     "tool_output": "PRIVATE_TOOL_TEXT" * 10000, "cursor": "tick-1"}}
    run = Mock(return_value={"reviews": {"a": accepted()}})
    _, _, cache = invoke({"a": concept()}, run, context=context)
    prompt = run.call_args.args[0]
    sent = json.loads(prompt.split("Teaching passages:\n", 1)[1])
    assert sent["a"]["concept"] == concept()
    assert len(sent["a"]["context"]["objective"]) == teaching.CONTEXT_LIMITS["objective"]
    assert len(sent["a"]["context"]["summary"]) == teaching.CONTEXT_LIMITS["summary"]
    assert sent["a"]["context"]["summary_truncated"] is True
    assert len(sent["a"]["context"]["source_ids"]) == 6
    assert "PRIVATE_TOOL_TEXT" not in prompt and "tick-1" not in prompt
    context["a"].update(cursor="tick-2", tool_output="New tool progress")
    no_call = Mock(side_effect=AssertionError("Tool progress is not teaching input"))
    invoke({"a": concept()}, no_call, context=context, cached_reviews=cache)
    no_call.assert_not_called()


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


@pytest.mark.parametrize("scope,field,replacement", [
    ("event", "next_action", "Wait for a recorded response before checking the condition"),
    ("event", "status", "failed"),
    ("event", "text", "The comparison failed because its hypothesis was not met"),
    ("event", "review_skipped", True),
    ("event", "review_source", "independent_reviewer"),
    ("event", "id", "changed-ninth-source"),
    ("task", "status", "done"),
    ("task", "summary", "The executor withdrew the earlier lower bound"),
    ("task", "acceptance_check", ["An explicit additional condition must hold"]),
    ("task", "outcome_source", {"status": "current_attempt", "attempt": 2, "event_id": "source-8"}),
])
def test_same_candidates_are_rechecked_when_the_selected_source_facts_change(scope, field, replacement):
    source = selected_source_context()
    response = {"reviews": {"a": accepted()}, "readings": {"a": accepted()}}
    candidates, readings = {"a": concept()}, {"a": reading()}
    _, first_receipts, cache = invoke(candidates, Mock(return_value=copy.deepcopy(response)),
                                      context={"a": source}, reading=readings)
    cached_snapshot = copy.deepcopy(cache)
    changed = copy.deepcopy(source)
    target = changed["task"] if scope == "task" else changed["events"][-1]
    target[field] = replacement
    inputs_before = copy.deepcopy((changed, candidates, readings))
    run = Mock(return_value=copy.deepcopy(response))
    _, receipts, updates = invoke(candidates, run, context={"a": changed}, reading=readings, cached_reviews=cache)
    run.assert_called_once()
    assert receipts["a"]["input_revision"] != first_receipts["a"]["input_revision"]
    assert set(updates).isdisjoint(cache)
    assert (changed, candidates, readings) == inputs_before
    assert cache == cached_snapshot


def test_unknown_tool_ticks_and_large_fields_are_not_source_evidence_or_cache_dependencies():
    source = selected_source_context()
    source.update(cursor="cursor-first", tool_output="PRIVATE_UNKNOWN_OUTPUT" * 10000,
                  generated_detail="GENERATED_PROSE_IS_NOT_SOURCE" * 1000)
    source["task"].update(tool_output="PRIVATE_UNKNOWN_OUTPUT" * 1000, cursor="task-cursor-first")
    source["events"][-1].update(tool_output="PRIVATE_UNKNOWN_OUTPUT" * 1000, cursor="event-cursor-first",
                                steps=[{"output": "PRIVATE_UNKNOWN_OUTPUT" * 1000}])
    before = copy.deepcopy(source)
    response = {"reviews": {"a": accepted()}, "readings": {"a": accepted()}}
    run = Mock(return_value=copy.deepcopy(response))
    _, _, cache = invoke({"a": concept()}, run, context={"a": source}, reading={"a": reading()})
    sent = json.loads(run.call_args.args[0].split("Teaching passages:\n", 1)[1])["a"]["context"]
    encoded = json.dumps(sent)
    assert "PRIVATE_UNKNOWN_OUTPUT" not in encoded and "GENERATED_PROSE_IS_NOT_SOURCE" not in encoded
    assert "cursor" not in encoded and "steps" not in encoded
    assert len(sent["source_ids"]) == 9
    assert source == before

    source.update(cursor="cursor-second", tool_output="another unrelated tool output")
    source["task"].update(cursor="task-cursor-second", tool_output="changed")
    source["events"][-1].update(cursor="event-cursor-second", tool_output="changed", steps=[])
    no_call = Mock(side_effect=AssertionError("Unrelated collection ticks must reuse this review"))
    _, _, updates = invoke({"a": concept()}, no_call, context={"a": source}, reading={"a": reading()}, cached_reviews=cache)
    no_call.assert_not_called()
    assert updates == {}


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


def test_unavailable_model_verdict_hides_example_and_is_cached():
    verdict = {"status": "unavailable", "reason": "The necessary fact cannot be checked from the supplied context.",
               "findings": [], "replacement": None}
    usable, receipts, cache = invoke({"a": concept()}, Mock(return_value={"reviews": {"a": verdict}}))
    assert usable["a"] is None and receipts["a"]["status"] == "unavailable"
    no_call = Mock(side_effect=AssertionError("No repeated request for the same unavailable example"))
    assert invoke({"a": concept()}, no_call, cached_reviews=cache)[0]["a"] is None
    no_call.assert_not_called()


@pytest.mark.parametrize("error", [OSError("service unavailable"), TimeoutError("deadline"), ValueError("invalid JSON")])
def test_runner_failure_makes_no_retry_and_preserves_other_cached_reviews(error):
    _, _, cache = invoke({"cached": concept()}, Mock(return_value={"reviews": {"cached": accepted()}}))
    run = Mock(side_effect=error)
    usable, receipts, updates = invoke({"cached": concept(), "new": {**concept(), "name": "New concept"}}, run, cached_reviews=cache)
    run.assert_called_once()
    assert usable["cached"] == concept() and usable["new"] is None
    assert receipts["new"]["error_code"] == "review_failed" and updates == {}


def test_omitted_card_in_batch_is_not_mistaken_for_acceptance():
    run = Mock(return_value={"reviews": {"a": accepted()}})
    usable, receipts, updates = invoke({"a": concept(), "b": {**concept(), "name": "Other"}}, run)
    assert usable == {"a": None, "b": None}
    assert all(row["error_code"] == "review_failed" for row in receipts.values())
    assert updates == {}


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


def test_oversized_candidate_is_not_silently_shortened_before_checking():
    oversized = {**concept(), "example": "x" * (teaching.CONCEPT_LIMITS["example"] + 1)}
    run = Mock(side_effect=AssertionError("Invalid candidate needs no model call"))
    usable, receipts, _ = invoke({"a": oversized}, run)
    assert usable["a"] is None and receipts["a"]["error_code"] == "invalid_concept"
    run.assert_not_called()


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
    }


def reading_correction():
    return {
        "status": "corrected", "reason": "Explain the purpose before using a specialist criterion name.",
        "findings": [{"field": "title", "quote": "isometry criterion", "kind": "undefined_term",
                      "reason": "The reader needs to know what is being checked without knowing this term."}],
        "replacement": {
            **reading(),
            "title": "Check whether the earlier method applies to these new objects",
            "why": "The task asks whether a method already used for one class of objects could work for another. This remains a possibility to check.",
        },
    }


def unavailable_decision():
    return {"status": "unavailable", "reason": "A faithful explanation cannot be confirmed from these records.",
            "findings": [], "replacement": None}


def test_supplied_reading_correction_is_atomic_and_separate_from_concept_acceptance():
    draft = reading()
    before = copy.deepcopy(draft)
    decision = reading_correction()
    run = Mock(return_value={"reviews": {"a": accepted()}, "readings": {"a": decision}})
    usable, receipts, updates = invoke({"a": concept()}, run, reading={"a": draft})
    run.assert_called_once()
    assert usable["a"] == concept() and receipts["a"]["status"] == "accepted"
    assert receipts["a"]["reading_review"]["status"] == "corrected"
    assert receipts["a"]["reading_review"]["kind"] == "model_readability_review"
    assert receipts["a"]["reading_replacement"] == decision["replacement"]
    assert receipts["a"]["reading_replacement"]["scope"] == before["scope"]
    assert receipts["a"]["reading_replacement"]["next"] == before["next"]
    assert draft == before and len(updates) == 1


def test_null_concept_still_checks_reading_without_inventing_a_concept_verdict():
    run = Mock(return_value={"reviews": {}, "readings": {"a": accepted()}})
    usable, receipts, updates = invoke({"a": None}, run, reading={"a": reading()})
    run.assert_called_once()
    prompt, schema = run.call_args.args
    passages = json.loads(prompt.split("Teaching passages:\n", 1)[1])
    assert passages["a"]["concept"] is None and passages["a"]["reading"] == reading()
    assert schema["properties"]["reviews"]["properties"] == {}
    assert usable["a"] is None and "status" not in receipts["a"]
    assert receipts["a"]["reading_review"]["status"] == "accepted"
    assert receipts["a"]["reading_replacement"] == reading() and len(updates) == 1


@pytest.mark.parametrize("field", ["title", "why", "scope", "next"])
def test_each_actual_reading_field_participates_in_the_review_cache_key(field):
    first = {"a": reading()}
    response = {"reviews": {"a": accepted()}, "readings": {"a": accepted()}}
    _, _, cache = invoke({"a": concept()}, Mock(return_value=response), reading=first)
    changed = copy.deepcopy(first)
    changed["a"][field] += " Changed."
    run = Mock(return_value=response)
    _, receipts, updates = invoke({"a": concept()}, run, reading=changed, cached_reviews=cache)
    run.assert_called_once()
    assert set(updates).isdisjoint(cache)
    assert receipts["a"]["reading_replacement"][field] == changed["a"][field]


def test_a_concept_only_acceptance_is_not_reused_as_a_first_screen_check():
    _, _, cache = invoke({"a": concept()}, Mock(return_value={"reviews": {"a": accepted()}}))
    run = Mock(return_value={"reviews": {"a": accepted()}, "readings": {"a": reading_correction()}})
    _, receipts, updates = invoke({"a": concept()}, run, reading={"a": reading()}, cached_reviews=cache)
    run.assert_called_once()
    assert receipts["a"]["reading_review"]["status"] == "corrected"
    assert set(updates).isdisjoint(cache)


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


def test_unavailable_concept_does_not_discard_a_reading_correction():
    run = Mock(return_value={"reviews": {"a": unavailable_decision()}, "readings": {"a": reading_correction()}})
    usable, receipts, _ = invoke({"a": concept()}, run, reading={"a": reading()})
    assert usable["a"] is None and receipts["a"]["status"] == "unavailable"
    assert receipts["a"]["reading_review"]["status"] == "corrected"
    assert receipts["a"]["reading_replacement"] == reading_correction()["replacement"]


def test_invalid_reading_finding_keeps_valid_concept_but_does_not_replace_task_facts():
    decision = reading_correction()
    decision["findings"][0]["quote"] = "Words absent from the original title"
    run = Mock(return_value={"reviews": {"a": accepted()}, "readings": {"a": decision}})
    usable, receipts, updates = invoke({"a": concept()}, run, reading={"a": reading()})
    assert usable["a"] == concept() and receipts["a"]["status"] == "accepted"
    assert receipts["a"]["reading_review"]["status"] == "unavailable"
    assert receipts["a"]["reading_review"]["error_code"] == "finding_does_not_quote_candidate"
    assert "reading_replacement" not in receipts["a"] and updates == {}
    run.assert_called_once()


def test_failed_model_call_preserves_original_reading_and_records_no_false_acceptance():
    draft = reading()
    run = Mock(side_effect=TimeoutError("Shared deadline elapsed"))
    usable, receipts, updates = invoke({"a": None}, run, reading={"a": draft})
    assert usable["a"] is None and "status" not in receipts["a"]
    assert receipts["a"]["reading_review"]["status"] == "unavailable"
    assert "reading_replacement" not in receipts["a"]
    assert draft == reading() and updates == {}
    run.assert_called_once()


def test_reading_candidates_are_not_truncated_or_silently_missing_fields():
    for draft in [{**reading(), "scope": "x" * (teaching.READING_LIMITS["scope"] + 1)},
                  {key: value for key, value in reading().items() if key != "next"}]:
        run = Mock(side_effect=AssertionError("Incomplete reading cannot be reviewed"))
        _, receipts, updates = invoke({"a": None}, run, reading={"a": draft})
        assert receipts["a"]["reading_review"]["error_code"] == "invalid_reading"
        assert "reading_replacement" not in receipts["a"] and updates == {}
        run.assert_not_called()


def test_different_first_screens_do_not_alias_just_because_the_concept_matches():
    readings = {"a": reading(), "b": {**reading(), "title": "A different current task"}}
    response = {"reviews": {key: accepted() for key in readings}, "readings": {key: accepted() for key in readings}}
    run = Mock(return_value=response)
    _, receipts, updates = invoke({"a": concept(), "b": concept()}, run, reading=readings)
    run.assert_called_once()
    assert receipts["a"]["input_revision"] != receipts["b"]["input_revision"]
    assert len(updates) == 2
