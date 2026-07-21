from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.event_catalog import EventType
from argus_skill.core.models import ReviewDecision
from argus_skill.engineer import runner
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _review(plan_signal: str, reason: str = "") -> ReviewDecision:
    return ReviewDecision(
        status="continue",
        reason=reason or "review reason",
        next_action="continue locally",
        planner_report={
            "forward_progress": False,
            "evidence_files": [
                {"path": "research/NO_GO.md", "why": "records the falsifier"},
            ],
            "plan_signal": plan_signal,
        },
    )


def test_reconsider_signal_builds_shadow_event() -> None:
    event = runner._plan_signal_event(
        _review("reconsider", "new evidence invalidated the plan")
    )

    assert event == {
        "type": EventType.LIFE_PLAN_SIGNAL,
        "mode": "shadow",
        "signal": "reconsider",
        "reason": "new evidence invalidated the plan",
        "streak": 1,
        "confirm_rounds": 2,
        "confirmed": False,
        "evidence_files": [
            {"path": "research/NO_GO.md", "why": "records the falsifier"},
        ],
    }


def test_continue_signal_does_not_emit_shadow_event() -> None:
    assert runner._plan_signal_event(_review("continue")) is None


def test_replan_requested_verdict_ends_round_immediately() -> None:
    status, reason = SupervisedEngineer._classify(
        review=ReviewDecision(
            status="replan_requested",
            reason="The immutable run identity requires a replacement plan.",
            next_action="Create a fresh identity.",
        ),
        no_progress_streak=0,
        no_progress_threshold=2,
        round_index=1,
        max_rounds=3,
    )

    assert status == "replan_requested"
    assert "replacement plan" in reason


def test_harness_scope_arbitration_does_not_rewrite_reviewer_fields() -> None:
    review = _review("continue")
    original_report = dict(review.planner_report)

    runner._promote_scope_change_to_replan(
        review,
        reason="Harness detected a cross-mission request.",
    )

    assert review.status == "continue"
    assert review.reason == "review reason"
    assert review.next_action == "continue locally"
    assert review.planner_report == original_report
    assert review.harness_control == {
        "force_replan": True,
        "reason": "Harness detected a cross-mission request.",
        "stage_reconciliation_required": True,
        "mission_scope_change_required": True,
    }


def test_dynamic_plan_defaults_off_for_behavior_compatibility(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_DYNAMIC_PLAN_MODE", raising=False)

    assert SupervisedConfig().dynamic_plan_mode == "off"


def _review_json(signal: str, reason: str = "") -> str:
    return _review_json_with_fields(signal, reason=reason)


def _review_json_with_fields(
    signal: str,
    *,
    reason: str = "",
    progress_class: str = "none",
    control: dict | None = None,
) -> str:
    payload: dict[str, object] = {
        "status": "continue",
        "reason": reason or "The current route needs project-level reconsideration.",
        "next_action": "Preserve evidence and wait for a replacement plan.",
        "progress_class": progress_class,
        "planner_report": {
            "forward_progress": progress_class in {"decision", "evidence"},
            "plan_signal": signal,
            "evidence_files": [
                {"path": "research/NO_GO.md", "why": "records the falsifier"},
            ],
        },
    }
    if control is not None:
        payload["control"] = control
    return json.dumps(payload)


def _done_json() -> str:
    return json.dumps(
        {
            "status": "done",
            "reason": "The task is complete.",
            "next_action": "",
        }
    )


def _upstream_stage_defect_json() -> str:
    return json.dumps({
        "status": "continue",
        "reason": (
            "The earliest broken stage is benchmark; run execution is unsupported."
        ),
        "next_action": "Return to benchmark and recertify before any GPU work.",
        "progress_class": "decision",
        "planner_report": {
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [],
        },
    })


def _manager_return_stage_defect_json() -> str:
    return json.dumps({
        "status": "continue",
        "reason": "Execution failed closed before model loading.",
        "next_action": (
            "Manager must preserve history, return structurally to `benchmark`, "
            "and recertify before another run."
        ),
        "progress_class": "decision",
        "planner_report": {
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [],
        },
    })


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


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


def test_active_mode_requires_two_consecutive_reconsider_signals(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="round one", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(message=_review_json("reconsider", "first confirmation")),
    )
    backend.queue("engineer-r2", CannedResponse(message="round two", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(message=_review_json("reconsider", "second confirmation")),
    )
    events: list[dict] = []

    status, rounds, _final, reason, thread_id = _engineer(backend).run(
        objective="replace a falsified plan",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            dynamic_plan_mode="active",
            dynamic_plan_confirm_rounds=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "replan_requested"
    assert len(rounds) == 2
    assert reason == "second confirmation"
    assert thread_id is None
    plan_events = [event for event in events if event["type"] == "life.plan.signal"]
    assert [event["confirmed"] for event in plan_events] == [False, True]
    assert all(event["mode"] == "active" for event in plan_events)


def test_upstream_stage_defect_preempts_next_engineer_round(tmp_path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "run",
        "stages": {
            "research": {"status": "done"},
            "plan": {"status": "done"},
            "benchmark": {"status": "done"},
            "run": {"status": "in_progress"},
        },
    }), encoding="utf-8")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="run attempt", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_upstream_stage_defect_json()))
    backend.queue("engineer-r2", CannedResponse(message="must not run", thread_id="t2"))
    events: list[dict] = []

    status, rounds, _final, reason, _thread_id = _engineer(backend).run(
        objective="execute the run stage",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3, dynamic_plan_mode="off"),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "replan_requested"
    assert len(rounds) == 1
    assert "earliest broken stage is benchmark" in reason.lower()
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "reviewer",
    ]
    report = rounds[-1].review.planner_report
    assert report == {
        "forward_progress": True,
        "plan_signal": "continue",
        "evidence_files": [],
    }
    assert rounds[-1].review.harness_control["stage_reconciliation_required"] is True
    assert rounds[-1].review.harness_control["earliest_broken_stage"] == "benchmark"
    signals = [event for event in events if event.get("signal") == "stage_reconciliation"]
    assert signals and signals[-1]["target_stage"] == "benchmark"


def test_manager_return_wording_also_preempts_next_engineer_round(
    tmp_path,
) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": "run",
        "stages": {
            "research": {"status": "done"},
            "plan": {"status": "done"},
            "benchmark": {"status": "done"},
            "run": {"status": "in_progress"},
        },
    }), encoding="utf-8")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="run attempt", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_manager_return_stage_defect_json()))
    backend.queue("engineer-r2", CannedResponse(message="must not run", thread_id="t2"))

    status, rounds, _final, reason, _thread_id = _engineer(backend).run(
        objective="execute the run stage",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3, dynamic_plan_mode="off"),
        workdir=tmp_path,
    )

    assert status == "replan_requested"
    assert len(rounds) == 1
    assert "failed closed before model loading" in reason
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "reviewer",
    ]


def test_confirmed_reconsider_preempts_reviewer_wait(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner.time, "sleep", lambda *_a, **_k: None)
    _write_record(tmp_path / ".argus_subagents", "train-1")

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="first probe", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(
            message=_review_json_with_fields(
                "reconsider",
                reason="first confirmation",
            )
        ),
    )
    backend.queue("engineer-r2", CannedResponse(message="second probe", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(
            message=_review_json_with_fields(
                "reconsider",
                reason="second confirmation",
                control={"action": "wait_for_subagent", "task_id": "train-1"},
            )
        ),
    )
    backend.queue("engineer-r3", CannedResponse(message="should not run", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_json()))
    events: list[dict] = []

    status, rounds, _final, reason, _thread_id = _engineer(backend).run(
        objective="replace a falsified plan",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            dynamic_plan_mode="active",
            dynamic_plan_confirm_rounds=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "replan_requested"
    assert len(rounds) == 2
    assert reason == "second confirmation"
    assert [label for (label, _p, _o) in backend.history] == [
        "engineer-r1",
        "reviewer",
        "engineer-r2",
        "reviewer",
    ]
    plan_events = [event for event in events if event["type"] == "life.plan.signal"]
    assert [event["confirmed"] for event in plan_events] == [False, True]
    assert not any(
        event["type"].startswith("round.background_wait") for event in events
    )


def test_shadow_mode_never_changes_control_flow(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="round one", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(message=_review_json("reconsider", "first confirmation")),
    )
    backend.queue("engineer-r2", CannedResponse(message="round two", thread_id="t1"))
    backend.queue(
        "reviewer",
        CannedResponse(message=_review_json("reconsider", "second confirmation")),
    )
    backend.queue("engineer-r3", CannedResponse(message="round three", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_json()))

    status, rounds, _final, _reason, _thread_id = _engineer(backend).run(
        objective="observe a falsified plan",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            dynamic_plan_mode="shadow",
            dynamic_plan_confirm_rounds=2,
            stall_threshold=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == 3
