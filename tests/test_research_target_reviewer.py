from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate

from argus_skill.core.models import RunnerResult
from argus_skill.core.research_contract import research_completion_issue
from argus_skill.manager.stage_decider import final_stage_completion_decision
from argus_skill.reviewer import RESEARCH_SCHEMA_PATH, Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_research_target_levels


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


def _schema_verdict(result: dict) -> dict:
    return {
        "status": "done",
        "reason": "bounded review certified",
        "next_action": "none",
        "operator_question": None,
        "achievement": None,
        "failure_cause": None,
        "failure_source": None,
        "scientific_decision": "uncertain",
        "failure_layer": None,
        "progress_class": "decision",
        "control": None,
        "scope": "bounded",
        "routing_decision": "",
        "routing_reason": "",
        "routing_handoff": "",
        "research_result": result,
        "planner_report": {
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [],
        },
        "checklist": [
            {"item": "review", "satisfied": True, "evidence": "independently checked"}
        ],
        "checklist_feedback": None,
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
            "control": None,
            "research_result": self.result,
            "planner_report": {"forward_progress": True},
            "checklist": [
                {"item": "review", "satisfied": True, "evidence": "checked"}
            ],
        }
        return RunnerResult(exit_code=0, agent_messages=[json.dumps(payload)])


class _SchemaReviewerBackend:
    def __init__(self, verdict: dict) -> None:
        self.verdict = verdict
        self.options = None
        self.prompt = ""

    def run_exec(self, **kwargs) -> RunnerResult:
        self.options = kwargs["options"]
        self.prompt = kwargs["prompt"]
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(self.verdict)],
        )


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


def test_research_vertical_requires_an_explicit_success_bar(tmp_path: Path) -> None:
    module = load_vertical("research", project_root=tmp_path)

    assert vertical_research_target_levels(module) == (
        "exploratory",
        "publishable",
        "doctoral",
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
def test_harness_does_not_second_guess_reviewer_result_labels(
    tmp_path: Path,
    result: dict,
) -> None:
    decision = _evaluate(tmp_path, "doctoral", result)

    assert decision.status == "done"
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


def test_bounded_doctoral_breakthrough_can_complete_item(
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

    assert decision.status == "done"


def test_bounded_doctoral_novelty_probe_can_complete_item(
    tmp_path: Path,
) -> None:
    decision = _evaluate(
        tmp_path,
        "doctoral",
        _result(
            "novelty_unverified",
            novelty="unverified",
            significance="unverified",
        ),
        scope="bounded",
    )

    assert decision.status == "done"
    assert decision.research_result is not None
    assert decision.research_result["novelty_status"] == "unverified"


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value"),
    [
        ("evidence", []),
        ("correctness_status", "uncertain"),
        ("statement_fidelity_status", "failed"),
    ],
)
def test_structured_result_fields_do_not_override_reviewer_verdict(
    tmp_path: Path,
    invalid_field: str,
    invalid_value: object,
) -> None:
    result = _result(
        "novelty_unverified",
        novelty="unverified",
        significance="unverified",
    )
    result[invalid_field] = invalid_value

    decision = _evaluate(tmp_path, "doctoral", result, scope="bounded")

    assert decision.status == "done"


def test_active_schema_reaches_bounded_completion_without_missing_result(
    tmp_path: Path,
) -> None:
    result = _result(
        "novelty_unverified",
        novelty="unverified",
        significance="unverified",
    )
    verdict = _schema_verdict(result)
    schema = json.loads(Path(RESEARCH_SCHEMA_PATH).read_text(encoding="utf-8"))
    validate(verdict, schema)
    persist_vertical(tmp_path, "math", research_target_level="doctoral")
    backend = _SchemaReviewerBackend(verdict)

    decision = Reviewer(backend).evaluate(
        objective="certify the bounded review",
        original_objective="certify the bounded review",
        round_index=1,
        session_id="mission",
        main_summary="six review gates independently certified",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="bounded",
    )

    assert backend.options is not None
    effective_schema = json.loads(
        Path(backend.options.output_schema_path).read_text(encoding="utf-8")
    )
    assert effective_schema == schema
    assert (
        "Do not use `research_incomplete` merely because later project work remains"
        in backend.prompt
    )
    assert decision.status == "done"
    assert decision.research_result == result
    issue = research_completion_issue(
        decision.research_result,
        research_target_level="doctoral",
        scope="bounded",
    )
    assert issue == ""
    event = decision.to_event_payload(round_index=1)
    assert event["type"] == "round.review.completed"
    assert event["research_result"] == result
    completion = final_stage_completion_decision(
        decision,
        current_stage="review",
        stage_order=("scope", "solve", "review"),
        vertical="math",
        mission_scope="bounded",
        research_target_level="doctoral",
    )
    assert completion is not None
    assert completion.action == "complete"


def test_math_without_target_preserves_research_pause_verdict(tmp_path: Path) -> None:
    verdict = _schema_verdict(_result("partial_result"))
    verdict["status"] = "research_incomplete"
    schema = json.loads(Path(RESEARCH_SCHEMA_PATH).read_text(encoding="utf-8"))
    validate(verdict, schema)
    persist_vertical(tmp_path, "math")
    backend = _SchemaReviewerBackend(verdict)

    decision = Reviewer(backend).evaluate(
        objective="review partial progress",
        original_objective="review partial progress",
        round_index=1,
        session_id="mission",
        main_summary="partial result only",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="bounded",
    )

    assert decision.status == "research_incomplete"
    assert decision.research_result is not None
