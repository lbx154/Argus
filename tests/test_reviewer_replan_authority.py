from __future__ import annotations

import json

from argus_skill import SkillLoop
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _review_json(status: str, reason: str, *, next_action: str = "") -> str:
    return json.dumps({
        "status": status,
        "reason": reason,
        "next_action": next_action,
        "failure_cause": "method_failure",
        "progress_class": "evidence",
        "checklist": [{
            "item": "baseline.correct_reproducible",
            "satisfied": False,
            "evidence": "red verifier",
        }],
        "planner_report": {
            "forward_progress": status == "continue",
            "plan_signal": (
                "reconsider" if status == "replan_requested" else "continue"
            ),
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


def test_repeated_reviewer_prose_does_not_force_harness_replan(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="first full run"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "continue",
        "chunk_kda verifier failed with CUDA illegal memory access.",
        next_action="Localize the first failing case.",
    )))
    backend.queue("engineer-r2", CannedResponse(message="second full run"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "continue",
        "The chunk_kda gate again failed with CUDA illegal memory access.",
        next_action="Inspect the next concrete failure.",
    )))
    backend.queue("engineer-r3", CannedResponse(message="fixed"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "done",
        "The Reviewer verified the repaired result.",
    )))

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="certify baseline",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            dynamic_plan_mode="off",
            stall_threshold=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == 3


def test_explicit_reviewer_replan_verdict_is_authoritative(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="inspected evidence"))
    backend.queue("reviewer", CannedResponse(message=_review_json(
        "replan_requested",
        "The current mission contract is invalidated by the new evidence.",
    )))

    status, rounds, _final, reason, _thread = _engineer(backend).run(
        objective="certify baseline",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4),
        workdir=tmp_path,
    )

    assert status == "replan_requested"
    assert len(rounds) == 1
    assert "invalidated" in reason


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
