from __future__ import annotations

import json

from argus_skill import SkillLoop
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.failure_signature import (
    review_failure_signature,
    signature_similarity,
)
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _review(blocker: str) -> ReviewDecision:
    return ReviewDecision(
        status="continue",
        reason=blocker,
        next_action="Localize the first failing case.",
        failure_cause="method_failure",
        progress_class="evidence",
        checklist=[{
            "item": "baseline.correct_reproducible",
            "satisfied": False,
            "evidence": "red verifier",
        }],
        planner_report={
            "forward_progress": True,
            "headline": "Baseline gate remains red.",
            "blocker": blocker,
            "recommended_next": "Localize the first failing case.",
            "plan_signal": "continue",
            "plan_signal_reason": "",
            "evidence_files": [],
        },
    )


def _review_json(blocker: str) -> str:
    review = _review(blocker)
    return json.dumps({
        "status": review.status,
        "reason": review.reason,
        "next_action": review.next_action,
        "failure_cause": review.failure_cause,
        "progress_class": review.progress_class,
        "round_summary_markdown": "# Review\n",
        "completion_summary_markdown": "",
        "checklist": review.checklist,
        "planner_report": review.planner_report,
    })


def _done_json() -> str:
    return json.dumps({
        "status": "done",
        "reason": "complete",
        "next_action": "",
        "round_summary_markdown": "# Done\n",
        "completion_summary_markdown": "done",
    })


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_repeated_review_signature_is_semantically_stable() -> None:
    first = review_failure_signature(_review(
        "chunk_kda verifier failed with 33 failures and CUDA illegal memory access."
    ))
    second = review_failure_signature(_review(
        "The frozen chunk_kda correctness oracle is red with 33 errors; "
        "CUDA illegal-memory-access prevents baseline timing."
    ))
    assert first is not None and second is not None
    assert signature_similarity(first, second) >= 0.62


def test_repeated_failure_forces_replan_even_when_dynamic_plan_is_off(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="first full run"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "chunk_kda verifier failed with CUDA illegal memory access."
    )))
    backend.queue("engineer-r2", CannedResponse(message="same full run again"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "The chunk_kda correctness gate again failed with CUDA illegal memory access."
    )))
    events: list[dict] = []

    def prompt_builder(_next_action, include_static=True):
        return "full static prompt" if include_static else "compact"

    status, rounds, _final, reason, _thread = _engineer(backend).run(
        objective="certify baseline",
        engineer_prompt_builder=prompt_builder,
        supervised_config=SupervisedConfig(
            max_rounds=10,
            dynamic_plan_mode="off",
            repeated_failure_threshold=2,
            repeated_failure_similarity=0.62,
            stall_threshold=0,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "replan_requested"
    assert len(rounds) == 2
    assert "cheapest targeted diagnostic" in reason
    assert rounds[-1].review.planner_report["plan_signal"] == "reconsider"
    repeated = [
        event for event in events
        if event.get("type") == "round.failure_signature.repeated"
    ]
    assert len(repeated) == 1
    starts = [event for event in events if event.get("type") == "round.start"]
    assert [event["prompt_mode"] for event in starts] == ["full", "compact"]
    assert starts[1]["prompt_chars"] < starts[0]["prompt_chars"]


def test_continuation_round_uses_reviewer_action_without_static_contract(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="round one"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "A distinct first failure needs a focused probe."
    )))
    backend.queue("engineer-r2", CannedResponse(message="round two"))
    backend.queue("reviewer", CannedResponse(message=_done_json()))
    calls: list[tuple[str | None, bool]] = []

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="finish",
        engineer_prompt_builder=lambda next_action, include_static=True: (
            calls.append((next_action, include_static)) or "prompt"
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            compact_continuation_prompts=True,
            repeated_failure_threshold=0,
            stall_threshold=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == 2
    assert calls == [(None, True), ("Localize the first failing case.", False)]


def test_compact_engineer_prompt_omits_static_skill_and_objective() -> None:
    full = SkillLoop._build_engineer_prompt(
        task="very long task " * 100,
        skill_text="very long skill " * 100,
        next_action=None,
        original_request="operator request " * 100,
        include_static=True,
        role_banner="role rules " * 100,
    )
    compact = SkillLoop._build_engineer_prompt(
        task="very long task " * 100,
        skill_text="very long skill " * 100,
        next_action="Run the single failing case.",
        original_request="operator request " * 100,
        include_static=False,
        role_banner="role rules " * 100,
    )

    assert "Run the single failing case" in compact
    assert "very long skill" not in compact
    assert "very long task" not in compact
    assert len(compact) < len(full) // 4
