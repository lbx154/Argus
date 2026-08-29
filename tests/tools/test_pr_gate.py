import subprocess
from subprocess import CompletedProcess
from unittest.mock import patch

from argus_skill.release_tools.pr_gate.criteria import (
    evaluate,
    file_type_consistency,
    scope_adequacy,
)
from argus_skill.release_tools.pr_gate.patch import (
    is_config_path,
    is_docs_path,
    patch_stats,
)


def test_scope_adequacy_flags_short_description_for_large_patch() -> None:
    score, _ = scope_adequacy("Fix bug", {"total_churn": 200})

    assert score == 0.2


def test_scope_adequacy_passes_when_patch_has_no_text_churn() -> None:
    score, evidence = scope_adequacy("Update the binary application icon.", {"total_churn": 0})

    assert score == 1.0
    assert evidence["not_applicable"] == "no_text_churn"


def test_json_config_detection_is_narrow() -> None:
    assert is_config_path("frontend/web/tsconfig.json")
    assert is_config_path("frontend/web/tsconfig.node.json")
    assert is_config_path("plugins/argus/.mcp.json")
    assert is_config_path("plugins/argus/.claude-plugin/plugin.json")
    assert is_config_path("argus_skill/release_manifest.json")
    assert not is_config_path("frontend/core/fixtures/eventCorpus.generated.json")
    assert not is_config_path("argus_skill/core/event_payload_schemas.json")
    assert not is_config_path("tests/domains/fixtures/example.json")


def test_docs_detection_excludes_runtime_markdown_assets() -> None:
    assert is_docs_path("docs/guide.md")
    assert is_docs_path("plugins/argus/README.md")
    assert is_docs_path("CHANGELOG.md")
    assert not is_docs_path("argus_skill/builtin_skills/agent-team-lead.md")
    assert not is_docs_path("plugins/argus/skills/argus-run/SKILL.md")


@patch("argus_skill.release_tools.pr_gate.patch.subprocess.run")
def test_patch_stats_keeps_git_rename_detection(run) -> None:
    run.side_effect = [
        CompletedProcess(args=[], returncode=0, stdout="merge-base\n", stderr=""),
        CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"0\t0\t\0src/guide.py\0tests/test_guide.py\0",
            stderr=b"",
        ),
    ]

    stats = patch_stats("base", "head")

    assert run.call_args_list[0].args[0] == ["git", "merge-base", "base", "head"]
    assert run.call_args_list[1].args[0] == [
        "git",
        "diff",
        "--numstat",
        "-z",
        "--find-renames",
        "merge-base",
        "head",
    ]
    assert stats["total_churn"] == 0
    assert stats["files_test_count"] == 1
    assert stats["files"] == ["tests/test_guide.py"]
    assert stats["renames"] == [
        {
            "old_path": "src/guide.py",
            "new_path": "tests/test_guide.py",
        }
    ]
    assert stats["merge_base_sha"] == "merge-base"


def test_patch_stats_excludes_changes_from_an_advanced_base(tmp_path) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "PR Gate Test")
    git("config", "user.email", "pr-gate@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    branch_point = git("rev-parse", "HEAD")

    git("switch", "-c", "feature")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_feature.py").write_text(
        "def test_feature():\n    assert True\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "add feature test")
    head = git("rev-parse", "HEAD")

    git("switch", "main")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "base-update.md").write_text(
        "Base-only documentation update.\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "advance base")
    advanced_base = git("rev-parse", "HEAD")

    stats = patch_stats(advanced_base, head, repo=tmp_path)

    assert stats["merge_base_sha"] == branch_point
    assert stats["comparison"] == "merge_base_to_head"
    assert stats["files"] == ["tests/test_feature.py"]
    assert stats["files_test_count"] == 1
    assert stats["files_docs_count"] == 0


def test_file_type_consistency_reports_missing_test_and_config_mentions() -> None:
    score, evidence = file_type_consistency(
        "Add copy actions to web conversations.",
        {
            "files_test_count": 1,
            "files_docs_count": 0,
            "files_config_count": 1,
        },
    )

    assert score == 0.0
    assert evidence["missing_categories"] == ["tests", "config"]
    assert evidence["categories"]["tests"]["mentioned"] is False
    assert evidence["categories"]["config"]["mentioned"] is False


def test_file_type_consistency_is_not_applicable_without_changed_categories() -> None:
    score, evidence = file_type_consistency(
        "Check whether documentation changes are reflected in the PR description.",
        {
            "files_test_count": 0,
            "files_docs_count": 0,
            "files_config_count": 0,
        },
    )

    assert score == 1.0
    assert evidence["not_applicable"] == "no_tracked_file_category_changed"
    assert evidence["changed_categories"] == []


def test_file_type_consistency_requires_every_changed_category() -> None:
    score, evidence = file_type_consistency(
        "Add tests for the new behavior.",
        {
            "files_test_count": 2,
            "files_docs_count": 1,
            "files_config_count": 0,
        },
    )

    assert score == 0.5
    assert evidence["mentioned_categories"] == ["tests"]
    assert evidence["missing_categories"] == ["docs"]


def test_file_type_consistency_matches_complete_tokens_only() -> None:
    score, evidence = file_type_consistency(
        "Improve the doctor, contest runner, and efficient cache.",
        {
            "files_test_count": 1,
            "files_docs_count": 1,
            "files_config_count": 1,
        },
    )

    assert score == 0.0
    assert evidence["mentioned_categories"] == []
    assert evidence["missing_categories"] == ["tests", "docs", "config"]


def test_file_type_consistency_accepts_all_changed_categories() -> None:
    score, evidence = file_type_consistency(
        "Add tests and documentation, then update the CI configuration.",
        {
            "files_test_count": 1,
            "files_docs_count": 1,
            "files_config_count": 1,
        },
    )

    assert score == 1.0
    assert evidence["missing_categories"] == []


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
