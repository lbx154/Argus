from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.release_tools.pr_gate.criteria import evaluate
from argus.release_tools.pr_gate.llm import (
    CopilotCLIClient,
    LLMError,
    LLMJudgeRequest,
    LLMJudgment,
    PromptLimits,
    _run_bounded,
    build_judge_prompt,
    parse_judgment,
)


def test_build_judge_prompt_bounds_untrusted_inputs() -> None:
    prompt = build_judge_prompt(
        LLMJudgeRequest(
            criterion="task_type_alignment",
            description="description" * 10,
            patch_summary="summary" * 10,
            patch_diff="diff" * 20,
        ),
        PromptLimits(
            description_chars=20,
            patch_summary_chars=20,
            patch_diff_chars=20,
            total_chars=2_000,
        ),
    )

    payload = json.loads(prompt.split("Payload:\n", 1)[1])
    assert payload["description_truncated"] is True
    assert payload["patch_summary_truncated"] is True
    assert payload["patch_diff_truncated"] is True
    assert "[truncated]" in payload["patch_diff"]


def test_parse_judgment_rejects_invalid_schema() -> None:
    with pytest.raises(LLMError, match="fields do not match"):
        parse_judgment(
            '{"criterion":"task_type_alignment","score":1}',
            expected_criterion="task_type_alignment",
        )


def test_escape_expansion_fails_with_bounded_prompt_error() -> None:
    with pytest.raises(LLMError) as error:
        build_judge_prompt(
            LLMJudgeRequest(
                criterion="task_type_alignment",
                description='\\\\"' * 1_000,
                patch_summary="summary",
            ),
            PromptLimits(
                description_chars=3_000,
                patch_summary_chars=100,
                patch_diff_chars=100,
                total_chars=2_000,
            ),
        )

    assert error.value.code == "prompt_too_large"


@patch("argus.release_tools.pr_gate.llm._run_bounded")
def test_copilot_cli_client_disables_tools_and_parses_json(run) -> None:
    run.return_value = (
        0,
        json.dumps(
            {
                "criterion": "task_type_alignment",
                "score": 0.9,
                "reason": "The requested task matches the patch.",
                "evidence": ["Both describe the same behavior."],
                "source_language": "en",
                "translated_description": None,
            }
        ).encode(),
        b"",
    )
    client = CopilotCLIClient(
        model="gpt-5.6-sol",
        working_directory=Path("/trusted"),
    )

    judgment = client.judge(
        LLMJudgeRequest(
            criterion="task_type_alignment",
            description="Fix the parser.",
            patch_summary="One parser file changed.",
        )
    )

    command = run.call_args.args[0]
    environment = run.call_args.kwargs["environment"]
    assert "--available-tools=" in command
    assert "--disable-builtin-mcps" in command
    assert "--no-custom-instructions" in command
    assert "--no-remote" in command
    assert "--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN" in command
    assert Path(environment["COPILOT_HOME"]).name.startswith("pr-gate-copilot-")
    assert judgment.score == 0.9


def test_bounded_runner_rejects_oversized_output(tmp_path) -> None:
    with pytest.raises(LLMError) as error:
        _run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 10000)",
            ],
            cwd=tmp_path,
            environment={},
            timeout_seconds=10,
            output_limit=100,
        )

    assert error.value.code == "response_too_large"


class _FailingClient:
    def judge(self, request: LLMJudgeRequest) -> LLMJudgment:
        raise LLMError("timeout", "timed out")


def test_llm_failure_makes_gate_incomplete() -> None:
    result = evaluate(
        "Fix the parser.",
        {
            "total_churn": 20,
            "files_test_count": 0,
            "files_docs_count": 0,
            "files_config_count": 0,
        },
        {
            "task_type_alignment": {
                "enabled": True,
                "uses_llm": True,
                "threshold": 0.8,
                "error_message": "Task type failed.",
            }
        },
        llm_client=_FailingClient(),
    )

    assert result["status"] == "incomplete"
    assert result["criteria"]["task_type_alignment"] == {
        "status": "unavailable",
        "uses_llm": True,
        "failure_code": "timeout",
    }


class _LowScoreClient:
    def judge(self, request: LLMJudgeRequest) -> LLMJudgment:
        return LLMJudgment(
            criterion=request.criterion,
            score=0.4,
            reason="The requested task does not match the patch.",
            evidence=("The description requests documentation only.",),
        )


def test_local_threshold_decides_llm_criterion_result() -> None:
    result = evaluate(
        "Update documentation.",
        {
            "total_churn": 20,
            "files_test_count": 0,
            "files_docs_count": 0,
            "files_config_count": 0,
        },
        {
            "task_type_alignment": {
                "enabled": True,
                "uses_llm": True,
                "threshold": 0.8,
                "error_message": "Task type failed.",
            }
        },
        llm_client=_LowScoreClient(),
    )

    criterion = result["criteria"]["task_type_alignment"]
    assert result["status"] == "flagged"
    assert criterion["status"] == "failed"
    assert criterion["score"] == 0.4
