"""The learning job record says why a mission was not learned, not just that it was not."""
import pytest

from argus.life.reflection_status import learning_status, observe_mission


def _run(tmp_path, result):
    observe_mission(tmp_path, "s", "m", lambda capture: dict(result), None)
    return learning_status(tmp_path, "s")["jobs"][0]


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


@pytest.mark.parametrize("result", [
    {"retired": ["lessons/old.md"]},
    {"reviewed_fact_recorded": True},
])
def test_retiring_a_page_or_keeping_a_reviewed_fact_is_learning(tmp_path, result):
    job = _run(tmp_path, {"skipped": "", **result})
    assert (job["status"], job["outcome"]["reason"]) == ("completed", "saved")
    assert job["outcome"]["counts"]["knowledge"] == 1


@pytest.mark.parametrize(("ignored", "detail"), [
    ([], "wrote_nothing"),
    (["/k/pages/lessons/INDEX.md"], "wrote_nothing"),
    (["/k/pages/lessons/broken.md"], "ignored_page"),
])
def test_a_reflection_that_changed_nothing_records_what_the_host_saw(tmp_path, ignored, detail):
    job = _run(tmp_path, {"skipped": "", "ignored": ignored})
    assert (job["status"], job["outcome"]["reason"]) == ("unchanged", "no_new_learning")
    assert job["outcome"]["detail"] == detail
