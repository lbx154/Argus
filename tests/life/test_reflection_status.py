"""The learning job record says why a mission was not learned, not just that it was not."""
import pytest

from argus.life.reflection_status import learning_status, observe_mission


def _run(tmp_path, result):
    observe_mission(tmp_path, "s", "m", lambda capture: dict(result), None)
    return learning_status(tmp_path, "s")["jobs"][0]


def test_call_refused_by_cost_policy_is_recorded_as_paused_not_failed(tmp_path):
    job = _run(tmp_path, {
        "failure": "[not dispatched] refused before start: unresolved provider cost: 1 call(s)",
        "stop_kind": "cost_unreconciled",
    })
    assert job["status"] == "skipped"
    assert job["outcome"]["reason"] == "paused"
    assert job["outcome"]["detail"] == "cost_unreconciled"


@pytest.mark.parametrize("stop_kind", ["budget_exhausted", "provider_cooldown"])
def test_other_host_refusals_are_paused_with_their_reason(tmp_path, stop_kind):
    job = _run(tmp_path, {"failure": "refused", "stop_kind": stop_kind})
    assert (job["status"], job["outcome"]["detail"]) == ("skipped", stop_kind)


def test_nothing_to_learn_is_not_a_failure(tmp_path):
    job = _run(tmp_path, {"skipped": "learning is off"})
    assert (job["status"], job["outcome"]["reason"]) == ("skipped", "not_needed")


def test_a_real_model_failure_stays_failed(tmp_path):
    job = _run(tmp_path, {"failure": "exit code 1", "stop_kind": "permanent_error"})
    assert (job["status"], job["outcome"]["reason"]) == ("failed", "failed")


def test_a_skip_records_the_reason_it_was_skipped(tmp_path):
    job = _run(tmp_path, {"skipped": "the final review found nothing new to learn"})
    assert job["outcome"]["detail"] == "the final review found nothing new to learn"


@pytest.mark.parametrize(("result", "detail"), [
    ({"retired": ["pages/lessons/a.md"], "ignored": ["/w/INDEX.md"], "wrote": "nothing"}, "retired_only"),
    ({"ignored": ["/w/pages/lessons/broken.md"], "wrote": "paths"}, "unreadable_page"),
    ({"ignored": ["/w/INDEX.md"], "wrote": "nothing"}, "wrote_nothing"),
    ({"reviewed_fact_recorded": True, "wrote": "nothing"}, "reviewed_fact_only"),
    ({"wrote": "paths"}, "claimed_write_missing"),
    ({}, "no_wrote_line"),
])
def test_an_unchanged_reflection_names_why_nothing_was_kept(tmp_path, result, detail):
    job = _run(tmp_path, {"skipped": "", "created": [], "updated": [], **result})
    assert (job["status"], job["outcome"]["detail"]) == ("unchanged", detail)
