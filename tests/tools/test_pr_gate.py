from tools.pr_gate.criteria import (
    evaluate,
    file_type_consistency,
    scope_adequacy,
)


def test_scope_adequacy_flags_short_description_for_large_patch() -> None:
    score, _ = scope_adequacy("Fix bug", {"total_churn": 200})

    assert score == 0.2


def test_file_type_consistency_reports_missing_test_and_config_mentions() -> None:
    score, evidence = file_type_consistency(
        "Add copy actions to web conversations.",
        {
            "files_test_count": 1,
            "files_docs_count": 0,
            "files_config_count": 1,
        },
    )

    assert score < 0.8
    assert evidence["tests"]["mentioned"] is False
    assert evidence["config"]["mentioned"] is False


def test_file_type_consistency_does_not_penalize_capability_descriptions() -> None:
    score, evidence = file_type_consistency(
        "Check whether documentation changes are reflected in the PR description.",
        {
            "files_test_count": 0,
            "files_docs_count": 0,
            "files_config_count": 0,
        },
    )

    assert score == 1.0
    assert evidence["docs"]["mentioned"] is True
    assert evidence["docs"]["score"] == 1.0


def test_disabled_llm_criterion_does_not_block_gate() -> None:
    result = evaluate(
        "A sufficiently detailed pull request description for a small patch.",
        {
            "total_churn": 10,
            "files_test_count": 0,
            "files_docs_count": 0,
            "files_config_count": 0,
        },
        {
            "scope_adequacy": {
                "enabled": True,
                "uses_llm": False,
                "threshold": 0.6,
                "error_message": "Scope failed.",
            },
            "task_type_alignment": {
                "enabled": False,
                "uses_llm": True,
                "error_message": "Task type failed.",
            },
        },
    )

    assert result["status"] == "passed"
    assert result["criteria"]["task_type_alignment"]["status"] == "disabled"
