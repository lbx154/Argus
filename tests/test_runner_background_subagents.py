"""Runner-level integration for the background-subagent advisory + cadence wait.

Drives :class:`SupervisedEngineer` with the deterministic ``MemoryBackend`` to
verify the engineer prompt gets the advisory, structured Engineer control yields
without burning a reviewer round, and that the feature is a no-op when there are
no in-flight subagents.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.engineer import runner as runner_module
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _write_record(reg: Path, task_id: str, **fields) -> None:
    reg.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task_id,
        "description": f"job {task_id}",
        "mode": "supervised",
        "state": "running",
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "monitor_interval": 30,
        "elapsed_seconds": 1000,
        "worker_pid": os.getpid(),
    }
    record.update(fields)
    (reg / f"{task_id}.json").write_text(json.dumps(record), encoding="utf-8")


def _write_external_record(reg: Path, work_id: str, **fields) -> None:
    reg.mkdir(parents=True, exist_ok=True)
    record = {
        "version": 1,
        "work_id": work_id,
        "state": "running_healthy",
        "heartbeat_at": time.time(),
        "stale_after_seconds": 1800,
        "poll_after_seconds": 30,
    }
    record.update(fields)
    (reg / f"{work_id}.json").write_text(json.dumps(record), encoding="utf-8")


def _review(
    *,
    status: str,
    reason: str,
    next_action: str,
    progress_class: str = "decision",
    control: dict | None = None,
) -> str:
    payload: dict[str, object] = {
        "status": status,
        "reason": reason,
        "next_action": next_action,
        "round_summary_markdown": "# review\n",
        "completion_summary_markdown": "Done." if status == "done" else "",
        "progress_class": progress_class,
        "planner_report": {
            "forward_progress": progress_class in {"decision", "evidence"},
            "headline": reason,
            "blocker": "",
            "recommended_next": next_action,
            "plan_signal": "continue",
            "plan_signal_reason": "",
            "evidence_files": [],
        },
    }
    if control is not None:
        payload["control"] = control
    return json.dumps(payload)


def _done_review() -> str:
    return _review(
        status="done",
        reason="Met criterion.",
        next_action="—",
        progress_class="decision",
    )


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _wait_control_response(wait_for: str, wait_id: str, message: str):
    def _factory(prompt, _options):
        marker = "write machine control to `"
        path = Path(prompt.split(marker, 1)[1].split("`", 1)[0])
        path.write_text(json.dumps({
            "review": "required",
            "skill_action": "none",
            "skill_name": "",
            "wait_for": wait_for,
            "wait_id": wait_id,
        }), encoding="utf-8")
        return message

    return _factory


def _legacy_control_wait_response(prompt, _options):
    marker = "write machine control to `"
    path = Path(prompt.split(marker, 1)[1].split("`", 1)[0])
    path.write_text(json.dumps({
        "review": "required",
        "skill_action": "none",
        "skill_name": "",
    }), encoding="utf-8")
    return "WAIT_FOR_SUBAGENT: train-1"


def test_structured_subagent_wait_skips_reviewer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Never really sleep during the cadence yield.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1", description="full GRPO run")

    backend = MemoryBackend()
    # Round 1: engineer yields to the self-watched subagent's cadence.
    backend.queue("engineer-r1", CannedResponse(
        message_factory=_wait_control_response(
            "subagent",
            "train-1",
            "Training is healthy; yielding to its registered supervisor cadence.",
        ),
        thread_id="t1",
    ))
    # Round 2: engineer does independent work; reviewer accepts it.
    backend.queue("engineer-r2", CannedResponse(
        message="Repaired the full evaluator while the run trains.", thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish the supervised run and evaluate",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=5),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    # The structured wait round skipped the reviewer: engineer-r2 is called directly
    # after engineer-r1, with no "reviewer" call in between.
    assert labels[0] == "engineer-r1"
    assert labels[1] == "engineer-r2"
    assert "reviewer" not in labels[:1]

    # A cadence wait happened and the mission completed on the working round.
    assert any(e.get("type") == "round.background_wait.started" for e in events)
    assert any(e.get("type") == "round.background_wait.completed" for e in events)
    assert status == "done"


def test_legacy_three_key_control_still_allows_sentinel_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1")
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message_factory=_legacy_control_wait_response),
    )
    backend.queue("engineer-r2", CannedResponse(message="Finished after wait."))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish after the supervised wait",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2),
        workdir=tmp_path,
    )

    assert status == "done"
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
        "reviewer",
    ]


def test_structured_external_work_wait_skips_reviewer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_external_record(tmp_path / ".argus_external_work", "experiment-1")
    monkeypatch.setattr(
        runner_module,
        "_run_external_work_wait",
        lambda **_kwargs: ("terminal", 30.0),
    )

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(
        message_factory=_wait_control_response(
            "external_work",
            "experiment-1",
            "Yielding to the registered external-work cadence.",
        ),
        thread_id="t1",
    ))
    backend.queue("engineer-r2", CannedResponse(
        message="Terminal evidence arrived; I evaluated it.",
        thread_id="t2",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="wait for and evaluate the external experiment",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert len(rounds) == 1
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
        "reviewer",
    ]
    assert any(
        event.get("type") == "round.external_work_wait.started"
        for event in events
    ) is False


def test_reviewer_wait_control_records_review_then_waits_without_incrementing_stall(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1", description="full GRPO run")

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Still waiting on the supervised checkpoint.",
        next_action="Use the next resumed round to inspect the fresh checkpoint.",
        progress_class="none",
    )))
    backend.queue("engineer-r2", CannedResponse(message="", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="The only productive move is to wait for train-1's next checkpoint.",
        next_action="When resumed, inspect the new checkpoint from train-1.",
        progress_class="none",
        control={"action": "wait_for_subagent", "task_id": "train-1"},
    )))
    backend.queue("engineer-r3", CannedResponse(
        message="Checkpoint landed; I inspected it and repaired the evaluator.",
        thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish the supervised run and evaluate",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=5,
            no_progress_threshold=2,
            stall_threshold=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    assert labels == [
        "engineer-r1",
        "reviewer",
        "engineer-r2",
        "reviewer",
        "engineer-r3",
        "reviewer",
    ]
    assert status == "done"
    assert len(rounds) == 3
    assert rounds[1].review.control_action == "wait_for_subagent"
    assert rounds[1].review.control_task_id == "train-1"
    assert not any(e.get("type") == "round.background_wait.rejected" for e in events)
    assert any(e.get("type") == "round.background_wait.started" for e in events)
    assert any(e.get("type") == "round.background_wait.completed" for e in events)
    stall_events = [e for e in events if e.get("type") == "round.stall"]
    assert stall_events
    assert max(int(e.get("semantic_stall_streak", 0) or 0) for e in stall_events) == 1
    review_idx = next(i for i, e in enumerate(events) if e.get("type") == "round.review.completed")
    wait_idx = next(i for i, e in enumerate(events) if e.get("type") == "round.background_wait.started")
    assert review_idx < wait_idx


def test_production_sequence_replay_validated_wait_does_not_become_second_stall(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1", description="production run")

    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message="Prepared harness inputs only.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Setup happened, but no decision evidence yet.",
        next_action="Run the validation slice.",
        progress_class="setup_only",
    )))
    backend.queue(
        "engineer-r2",
        CannedResponse(message="Collected validation evidence.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Validation evidence reset the semantic-stall streak.",
        next_action="Prepare the production wait handoff.",
        progress_class="evidence",
    )))
    backend.queue(
        "engineer-r3",
        CannedResponse(message="Synced only the wait prerequisites.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Setup-only handoff before the production wait.",
        next_action="Validate that train-1 is the correct self-watched run.",
        progress_class="setup_only",
    )))
    backend.queue(
        "engineer-r4",
        CannedResponse(message="Validated train-1 as the run to wait for.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="train-1 is self-watched; wait for its next checkpoint.",
        next_action="After the wait, inspect the new checkpoint.",
        progress_class="setup_only",
        control={"action": "wait_for_subagent", "task_id": "train-1"},
    )))
    backend.queue(
        "engineer-r5",
        CannedResponse(message="Checkpoint landed; mission is complete.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="replay production setup/evidence/setup/wait sequence",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=6,
            no_progress_threshold=10,
            stall_threshold=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    assert labels == [
        "engineer-r1",
        "reviewer",
        "engineer-r2",
        "reviewer",
        "engineer-r3",
        "reviewer",
        "engineer-r4",
        "reviewer",
        "engineer-r5",
        "reviewer",
    ]
    assert status == "done"
    assert len(rounds) == 5
    assert [round.review.progress_class for round in rounds[:4]] == [
        "setup_only",
        "evidence",
        "setup_only",
        "setup_only",
    ]
    assert rounds[3].review.control_action == "wait_for_subagent"
    assert rounds[3].review.control_task_id == "train-1"

    wait_events = [
        e for e in events if e.get("type") == "round.background_wait.started"
    ]
    assert [e.get("round_index") for e in wait_events] == [4]
    assert not any(e.get("type") == "round.background_wait.rejected" for e in events)
    stall_events = [e for e in events if e.get("type") == "round.stall"]
    assert [(e.get("round_index"), e.get("semantic_stall_streak")) for e in stall_events] == [
        (1, 1),
        (3, 1),
    ]


def test_reviewed_wait_uses_fresh_decision_anchor_after_decision_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_record(tmp_path / ".argus_subagents", "train-1", description="full GRPO run")

    class _FakeMonotonic:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            if self.calls == 1:
                return 0.0
            return 1000.0 + float(self.calls)

    monkeypatch.setattr(runner_module.time, "monotonic", _FakeMonotonic())
    monkeypatch.setattr(
        runner_module,
        "_run_background_wait",
        lambda **_kwargs: ("cadence_elapsed", 30.0),
    )

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Fresh decision landed; wait for the supervised checkpoint.",
        next_action="When resumed, inspect the next train-1 checkpoint.",
        progress_class="decision",
        control={"action": "wait_for_subagent", "task_id": "train-1"},
    )))
    backend.queue("engineer-r2", CannedResponse(message="", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Still integrating; no new decision yet.",
        next_action="Finish the integration work.",
        progress_class="none",
    )))
    backend.queue("engineer-r3", CannedResponse(
        message="Integration complete after the resumed wait.",
        thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish the supervised run and evaluate",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=5,
            decision_progress_timeout_seconds=100,
            no_progress_threshold=3,
            stall_threshold=2,
        ),
        workdir=tmp_path,
        on_event=None,
    )

    assert status == "done"
    assert len(rounds) == 3
    assert [label for (label, _p, _o) in backend.history] == [
        "engineer-r1",
        "reviewer",
        "engineer-r2",
        "reviewer",
        "engineer-r3",
        "reviewer",
    ]


def test_invalid_reviewer_wait_control_is_rejected_and_classifies_normally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    registry = tmp_path / ".argus_subagents"
    _write_record(registry, "direct-1", mode="direct")
    _write_record(registry, "unhealthy-1", last_supervisor_health="diverging")
    stale_path = registry / "stale-1.json"
    _write_record(registry, "stale-1", monitor_interval=30)
    old = time.time() - (900 * 2 + 5)
    os.utime(stale_path, (old, old))
    _write_record(registry, "terminal-1", state="done")

    cases = [
        ("ghost", "unknown task"),
        ("stale-1", "stale"),
        ("unhealthy-1", "health=diverging"),
        ("direct-1", "direct mode"),
        ("terminal-1", "terminal"),
    ]
    for task_id, expected_reason in cases:
        backend = MemoryBackend()
        backend.queue("engineer-r1", CannedResponse(message="No new evidence yet.", thread_id="t1"))
        backend.queue("reviewer", CannedResponse(message=_review(
            status="continue",
            reason=f"Reviewer asked to wait on {task_id}.",
            next_action="Keep waiting.",
            progress_class="none",
            control={"action": "wait_for_subagent", "task_id": task_id},
        )))

        events: list[dict] = []
        status, rounds, _final, _reason, _tid = _engineer(backend).run(
            objective=f"wait on {task_id}",
            engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
            supervised_config=SupervisedConfig(
                max_rounds=3,
                stall_threshold=1,
            ),
            workdir=tmp_path,
            on_event=events.append,
        )

        assert status == "no_progress"
        assert len(rounds) == 1
        assert rounds[0].review.control_task_id == task_id
        assert any(e.get("type") == "round.review.completed" for e in events)
        rejected = next(
            e for e in events if e.get("type") == "round.background_wait.rejected"
        )
        assert expected_reason in str(rejected.get("text", ""))
        assert not any(e.get("type") == "round.background_wait.started" for e in events)


def test_review_wait_rejection_does_not_traverse_outside_registry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    (tmp_path / ".argus_subagents").mkdir()
    (tmp_path / "leak.json").write_text(
        json.dumps({"state": "done", "secret": "TOP_SECRET"}),
        encoding="utf-8",
    )

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="No new evidence yet.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_review(
        status="continue",
        reason="Reviewer asked to wait on ../leak.",
        next_action="Keep waiting.",
        progress_class="none",
        control={"action": "wait_for_subagent", "task_id": "../leak"},
    )))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="wait on ../leak",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            stall_threshold=1,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "no_progress"
    assert len(rounds) == 1
    rejected = next(
        e for e in events if e.get("type") == "round.background_wait.rejected"
    )
    assert rejected.get("reason_code") == "unknown_task"
    assert "../leak" in str(rejected.get("text", ""))
    assert "state=done" not in str(rejected.get("text", ""))
    assert "TOP_SECRET" not in str(rejected.get("text", ""))
    assert not any(e.get("type") == "round.background_wait.started" for e in events)


def test_unknown_wait_target_falls_through_to_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    # No registry: the named job is not waitable, so the sentinel is ignored and
    # the round is reviewed normally.
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="WAIT_FOR_SUBAGENT: ghost", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for (label, _p, _o) in backend.history]
    assert labels[0] == "engineer-r1"
    assert "reviewer" in labels  # the round WAS reviewed (no spurious wait)
    assert not any(e.get("type") == "round.background_wait.started" for e in events)
    assert status == "done"


def test_no_advisory_without_inflight_subagents(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Did the work.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: p for (label, p, _o) in backend.history}
    assert "Background subagents in flight" not in prompts["engineer-r1"]


def test_advisory_reaches_engineer_and_reviewer(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "train-1")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Did independent work.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: prompt for (label, prompt, _options) in backend.history}
    assert "Background subagents in flight" in prompts["engineer-r1"]
    assert "train-1" in prompts["engineer-r1"]
    assert "Background subagents in flight" in prompts["reviewer"]
    assert "train-1" in prompts["reviewer"]


def test_reviewer_advisory_refreshes_after_engineer_turn(tmp_path: Path) -> None:
    registry = tmp_path / ".argus_subagents"
    record_path = registry / "train-1.json"
    _write_record(registry, "train-1")
    backend = MemoryBackend()

    def complete_subagent(_prompt, _options) -> str:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["state"] = "completed"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        return "Finished independent work."

    backend.queue(
        "engineer-r1",
        CannedResponse(message_factory=complete_subagent, thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: prompt for (label, prompt, _options) in backend.history}
    assert "Background subagents in flight" in prompts["engineer-r1"]
    assert "Background subagents in flight" not in prompts["reviewer"]


def test_flag_disables_advisory(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "train-1")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Did the work.", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    _engineer(backend).run(
        objective="do the thing",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3, background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=None,
    )

    prompts = {label: p for (label, p, _o) in backend.history}
    assert "Background subagents in flight" not in prompts["engineer-r1"]
