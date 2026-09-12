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
        monkeypatch.setattr(teaching, "TEACHING_REVIEW_VERSION", 2)
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
