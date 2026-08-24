from __future__ import annotations

from pathlib import Path

from argus_skill.release_tools.check_repository_parity import (
    is_private_only,
    unexpected_differences,
)


def test_private_only_allowlist_is_narrow() -> None:
    assert is_private_only("technical_report/sections/01_introduction.tex")
    assert is_private_only("docs/evaluations/run.md")
    assert is_private_only("PRIVATE_TODO.md")
    assert is_private_only(".github/workflows/private-public-parity.yml")
    assert is_private_only("./.github/workflows/private-public-parity.yml")
    assert is_private_only("tests/test_operator_output_examples.py")
    assert not is_private_only("argus_skill/roles/prompts/manager.py")
    assert not is_private_only("README.md")
    assert not is_private_only("docs/FEATURES.md")


def test_workflow_runs_stdlib_checker_without_importing_package() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "private-public-parity.yml"
    ).read_text(encoding="utf-8")

    assert "python argus_skill/release_tools/check_repository_parity.py" in workflow
    assert "python -m argus_skill.release_tools.check_repository_parity" not in workflow


def test_unexpected_differences_are_normalized_and_sorted() -> None:
    assert unexpected_differences(
        [
            "technical_report/main.tex",
            "argus_skill/core/usage.py",
            "README.md",
            "argus_skill/core/usage.py",
        ]
    ) == ["README.md", "argus_skill/core/usage.py"]
