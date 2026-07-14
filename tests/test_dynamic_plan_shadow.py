from __future__ import annotations

import json

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
        reason="review reason",
        next_action="continue locally",
        planner_report={
            "forward_progress": False,
            "headline": "route assessment",
            "blocker": "the current route no longer supports the objective",
            "recommended_next": "ask L4 to reconsider the remaining plan",
            "evidence_files": [
                {"path": "research/NO_GO.md", "why": "records the falsifier"},
            ],
            "plan_signal": plan_signal,
            "plan_signal_reason": reason,
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


def test_dynamic_plan_defaults_off_for_behavior_compatibility(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_DYNAMIC_PLAN_MODE", raising=False)

    assert SupervisedConfig().dynamic_plan_mode == "off"


def _review_json(signal: str, reason: str = "") -> str:
    return json.dumps(
        {
            "status": "continue",
            "reason": "The current route needs project-level reconsideration.",
            "next_action": "Preserve evidence and wait for a replacement plan.",
            "round_summary_markdown": "# Review\n",
            "completion_summary_markdown": "",
            "planner_report": {
                "forward_progress": False,
                "headline": "route falsified",
                "blocker": "the remaining plan assumes a falsified mechanism",
                "recommended_next": "replace the remaining plan",
                "plan_signal": signal,
                "plan_signal_reason": reason,
                "evidence_files": [
                    {"path": "research/NO_GO.md", "why": "records the falsifier"},
                ],
            },
        }
    )


def _done_json() -> str:
    return json.dumps(
        {
            "status": "done",
            "reason": "The task is complete.",
            "next_action": "",
            "round_summary_markdown": "# Done\n",
            "completion_summary_markdown": "Done.",
        }
    )


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


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
