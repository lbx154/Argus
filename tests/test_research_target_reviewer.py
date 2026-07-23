from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.reviewer import Reviewer, ReviewerConfig, SCHEMA_PATH
from argus_skill.skills.vertical_select import persist_vertical


class _Backend:
    def __init__(self, status: str) -> None:
        self.status = status
        self.prompt = ""
        self.options = None

    def run_exec(self, **kwargs) -> RunnerResult:
        self.prompt = kwargs["prompt"]
        self.options = kwargs["options"]
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps({
                "status": self.status,
                "reason": "Independent judgment from the inspected evidence.",
                "next_action": "" if self.status == "done" else "Choose a better direction.",
                "operator_question": None,
            })],
        )


def test_research_target_is_prompt_context_not_output_schema(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", research_target_level="publishable")
    backend = _Backend("replan_requested")
    decision = Reviewer(backend).evaluate(
        objective="produce publishable research",
        original_objective="produce publishable research",
        round_index=1,
        session_id="mission",
        main_summary="evidence landed",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="bounded",
    )

    assert decision.status == "replan_requested"
    assert "Project target: `publishable`" in backend.prompt
    assert backend.options.output_schema_path
    assert Path(backend.options.output_schema_path).name.startswith(
        Path(SCHEMA_PATH).stem
    )


def test_reviewer_verdict_is_not_rewritten_from_formal_result_labels(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "research", research_target_level="doctoral")
    backend = _Backend("done")
    decision = Reviewer(backend).evaluate(
        objective="judge the current result",
        round_index=1,
        session_id="mission",
        main_summary="current evidence",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="final_submission",
    )

    assert decision.status == "done"
