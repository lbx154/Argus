"""A durable wait may defer computation, but cannot bypass a new result review."""
from __future__ import annotations

import json
import os
from typing import Any, cast

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig


def job(workdir, *, run_id="run-1", state="running", heartbeat=100):
    root = workdir / ".argus_subagents"
    root.mkdir(exist_ok=True)
    (root / "train.json").write_text(json.dumps({
        "task_id": "train", "run_id": run_id, "state": state,
        "mode": "direct", "worker_pid": os.getpid(), "heartbeat_at": heartbeat,
    }))


class Engineer:
    calls = 0

    def run_exec(self, **kwargs):
        self.calls += 1
        return RunnerResult(exit_code=0, agent_messages=[
            'Implemented the experiment and inspected the previous result.\n'
            '{"wait_for": "subagent", "wait_id": "train"}'
        ])


class Reviewer:
    def __init__(self, status="continue"):
        self.calls = 0
        self.status = status

    def evaluate(self, **kwargs):
        self.calls += 1
        return ReviewDecision(status=self.status, reason="Checked the actual result and current experiment.", next_action="Continue after the existing run.")


def execute(tmp_path, monkeypatch, reviewer, *, independent=True, rounds=4):
    from argus.engineer import runner

    def wait(**kw):
        if kw.get("on_event"):
            kw["on_event"]({"type": "test.wait"})
        return "cadence_elapsed", 30.0

    monkeypatch.setattr(runner, "_run_external_work_wait", wait)
    engineer = Engineer()
    value = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    value.engineer_runner = engineer
    value.engineer_config = EngineerConfig(model="stub")
    value.reviewer = reviewer
    value.reviewer_config = ReviewerConfig(model="stub")
    config = SupervisedConfig(
        max_rounds=rounds, require_independent_review=independent,
        background_subagent_advisory=True,
        context_packet_path=tmp_path / "handoff" / "mission.json",
    )
    events = []
    result = value.run(
        objective="Train and evaluate a useful policy", workdir=tmp_path,
        engineer_prompt_builder=lambda action, include_static=True: action or "Start",
        supervised_config=config, on_event=events.append,
    )
    return result, events, engineer


def test_new_run_reviewed_once_and_heartbeat_wait_survives_restart(tmp_path, monkeypatch):
    job(tmp_path)
    reviewer = Reviewer()
    first, events, _ = execute(tmp_path, monkeypatch, reviewer)
    assert first[0] == "paused_external_work"
    assert reviewer.calls == 1
    kinds = [e["type"] for e in events]
    assert kinds.index("round.review.completed") < kinds.index("test.wait")
    job(tmp_path, heartbeat=200)
    second, _, engineer = execute(tmp_path, monkeypatch, reviewer)
    assert second[0] == "paused_external_work"
    assert reviewer.calls == 1 and engineer.calls == 1


def test_resubmitted_run_requires_a_fresh_review(tmp_path, monkeypatch):
    job(tmp_path)
    reviewer = Reviewer()
    execute(tmp_path, monkeypatch, reviewer)
    job(tmp_path, run_id="run-2")
    result, _, _ = execute(tmp_path, monkeypatch, reviewer)
    assert result[0] == "paused_external_work"
    assert reviewer.calls == 2


def test_terminal_result_requires_review_even_if_engineer_requests_wait(tmp_path, monkeypatch):
    job(tmp_path)
    reviewer = Reviewer()
    execute(tmp_path, monkeypatch, reviewer)
    job(tmp_path, state="done")
    reviewer.status = "done"
    result, events, _ = execute(tmp_path, monkeypatch, reviewer)
    assert result[0] == "done" and reviewer.calls == 2
    assert any(e["type"] == "round.external_work_review.required" and e["phase"] == "terminal" for e in events)


def test_active_background_run_cannot_be_certified_complete(tmp_path, monkeypatch):
    job(tmp_path)
    reviewer = Reviewer("done")
    result, _, _ = execute(tmp_path, monkeypatch, reviewer)
    assert result[0] == "paused_external_work" and reviewer.calls == 1


def test_missions_without_independent_review_keep_model_free_wait(tmp_path, monkeypatch):
    job(tmp_path)
    reviewer = Reviewer()
    result, _, engineer = execute(tmp_path, monkeypatch, reviewer, independent=False)
    assert result[0] == "paused_external_work"
    assert reviewer.calls == 0 and engineer.calls == 1
