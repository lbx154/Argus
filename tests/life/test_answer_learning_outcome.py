"""The learning job record says why a turn was not learned, not just that it was not."""
import pytest

from argus.life import reflection
from argus.life.answer_learning import enqueue_answer, learning_status, retry_learning


def _run(tmp_path, monkeypatch, result):
    (tmp_path / "projects" / "s").mkdir(parents=True)
    monkeypatch.setattr(reflection, "reflect_after_answer", lambda **_: dict(result))
    enqueue_answer(root=tmp_path, sid="s", operator_text="hi", reply="hello",
                   turn_id="t", backend=object()).join(10)
    return learning_status(tmp_path, "s")["jobs"][0]


def test_call_refused_by_cost_policy_is_recorded_as_paused_not_failed(tmp_path, monkeypatch):
    job = _run(tmp_path, monkeypatch, {
        "failure": "[not dispatched] refused before start: unresolved provider cost: 1 call(s)",
        "stop_kind": "cost_unreconciled",
    })
    assert job["status"] == "skipped"
    assert job["outcome"]["reason"] == "paused"
    assert job["outcome"]["detail"] == "cost_unreconciled"
    monkeypatch.setattr(reflection, "reflect_after_answer", lambda **_: {})
    assert retry_learning(tmp_path, "s", job["id"])


@pytest.mark.parametrize("stop_kind", ["budget_exhausted", "provider_cooldown"])
def test_other_host_refusals_are_paused_with_their_reason(tmp_path, monkeypatch, stop_kind):
    job = _run(tmp_path, monkeypatch, {"failure": "refused", "stop_kind": stop_kind})
    assert (job["status"], job["outcome"]["detail"]) == ("skipped", stop_kind)


def test_nothing_to_learn_is_not_a_failure(tmp_path, monkeypatch):
    job = _run(tmp_path, monkeypatch, {"skipped": "nothing was said"})
    assert (job["status"], job["outcome"]["reason"]) == ("skipped", "not_needed")
    assert not retry_learning(tmp_path, "s", job["id"])


def test_a_real_model_failure_stays_failed(tmp_path, monkeypatch):
    job = _run(tmp_path, monkeypatch, {"failure": "exit code 1", "stop_kind": "permanent_error"})
    assert (job["status"], job["outcome"]["reason"]) == ("failed", "failed")
