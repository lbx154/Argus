from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical


def _result(
    result_class: str,
    *,
    novelty: str = "not_applicable",
    significance: str = "exploratory",
) -> dict:
    return {
        "result_class": result_class,
        "correctness_status": "verified",
        "novelty_status": novelty,
        "significance_status": significance,
        "statement_fidelity_status": "verified",
        "evidence": ["fresh independent evidence"],
        "limitations": [],
    }


class _ReviewerBackend:
    def __init__(self, result: dict) -> None:
        self.result = result

    def run_exec(self, **_kwargs) -> RunnerResult:
        payload = {
            "status": "done",
            "reason": "reviewed",
            "next_action": "none",
            "round_summary_markdown": "# Review\n",
            "completion_summary_markdown": "# Complete\n",
            "research_result": self.result,
            "planner_report": {"forward_progress": True},
            "checklist": [
                {"item": "review", "satisfied": True, "evidence": "checked"}
            ],
        }
        return RunnerResult(exit_code=0, agent_messages=[json.dumps(payload)])


def _evaluate(tmp_path: Path, target: str, result: dict, *, scope: str = ""):
    persist_vertical(tmp_path, "math", research_target_level=target)
    return Reviewer(_ReviewerBackend(result)).evaluate(
        objective="research the theorem",
        original_objective="research the theorem",
        round_index=1,
        session_id="mission",
        main_summary="evidence landed",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope=scope,
    )


@pytest.mark.parametrize(
    "result",
    [
        _result("finite_verification"),
        _result(
            "novelty_unverified",
            novelty="unverified",
            significance="unverified",
        ),
        _result("structured_failure_report"),
    ],
)
def test_doctoral_non_breakthrough_verdict_is_not_done(
    tmp_path: Path,
    result: dict,
) -> None:
    decision = _evaluate(tmp_path, "doctoral", result)

    assert decision.status != "done"
    assert decision.research_result is not None


def test_doctoral_verified_new_doctoral_verdict_is_done(tmp_path: Path) -> None:
    decision = _evaluate(
        tmp_path,
        "doctoral",
        _result(
            "new_theorem",
            novelty="verified_new",
            significance="doctoral",
        ),
    )

    assert decision.status == "done"
    assert decision.research_result is not None
    assert decision.research_result["correctness_status"] == "verified"
    assert decision.research_result["novelty_status"] == "verified_new"
    assert decision.research_result["significance_status"] == "doctoral"


def test_exploratory_honest_failure_verdict_can_finish(tmp_path: Path) -> None:
    decision = _evaluate(
        tmp_path,
        "exploratory",
        _result("structured_failure_report"),
    )

    assert decision.status == "done"


def test_bounded_doctoral_breakthrough_does_not_certify_mission(
    tmp_path: Path,
) -> None:
    decision = _evaluate(
        tmp_path,
        "doctoral",
        _result(
            "new_theorem",
            novelty="verified_new",
            significance="doctoral",
        ),
        scope="bounded",
    )

    assert decision.status != "done"
