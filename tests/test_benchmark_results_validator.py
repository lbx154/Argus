from __future__ import annotations

import re
import subprocess
from pathlib import Path

from benchmarks.validate_results import (
    validate_bundle_dir,
    validate_results_root,
)


def _write_file(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_complete_bundle(root: Path, *, with_exempt: bool = False) -> Path:
    bundle = root / "prompt-only-tb2-smoke-20260515T1435Z"
    _write_file(bundle / "PLAN.md")
    _write_file(bundle / "BUILD_INFO.md")
    _write_file(bundle / "RESULTS.md")
    _write_file(bundle / "manifest.json", '{"source_run_root":"./scratch"}\n')
    _write_file(bundle / "logs" / "export.log")
    _write_file(
        bundle / "summary.tsv",
        "\t".join(
            [
                "order",
                "condition",
                "task_id",
                "zero_touch_success",
                "human_interactions_after_assignment",
                "active_touch_minutes_after_assignment",
                "manual_commands",
                "manual_rescue",
                "intervention_severity",
                "result_json",
                "stdout_log",
                "stderr_log",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "1",
                "codex",
                "cancel-async-tasks",
                "True",
                "0",
                "0.0",
                "0",
                "none",
                "reviewer_off_shortcut",
                "jobs/raw/o001/result.json",
                "jobs/raw/o001/stdout.log",
                "jobs/raw/o001/stderr.log",
            ]
        )
        + "\n",
    )
    _write_file(bundle / "jobs" / "raw" / "o001" / "result.json")
    _write_file(bundle / "jobs" / "raw" / "o001" / "stdout.log")
    _write_file(bundle / "jobs" / "raw" / "o001" / "stderr.log")
    _write_file(bundle / "jobs" / "raw" / "o001" / "metadata.json")
    _write_file(bundle / "jobs" / "raw" / "o001" / "prompt.txt")
    _write_file(bundle / "jobs" / "raw" / "o001" / "verification-reward-latest" / "official-verifier.log")
    _write_file(
        bundle / "jobs" / "index.tsv",
        "\t".join(
            [
                "job_id",
                "condition",
                "task_id",
                "bundle_dir",
                "result_json",
                "stdout_log",
                "stderr_log",
                "metadata_json",
                "prompt_txt",
                "verification_log",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "o001",
                "codex",
                "cancel-async-tasks",
                "jobs/raw/o001",
                "jobs/raw/o001/result.json",
                "jobs/raw/o001/stdout.log",
                "jobs/raw/o001/stderr.log",
                "jobs/raw/o001/metadata.json",
                "jobs/raw/o001/prompt.txt",
                "jobs/raw/o001/verification-reward-latest/official-verifier.log",
            ]
        )
        + "\n",
    )
    if with_exempt:
        _write_file(bundle / "EXEMPT.md", "legacy\n")
    return bundle


def test_validate_complete_experiment_dir(tmp_path: Path) -> None:
    bundle = _make_complete_bundle(tmp_path)
    assert validate_bundle_dir(bundle) == []


def test_validate_bundle_dir_requires_index_paths_to_resolve(tmp_path: Path) -> None:
    bundle = _make_complete_bundle(tmp_path)
    _write_file(
        bundle / "jobs" / "index.tsv",
        "\t".join(
            [
                "job_id",
                "condition",
                "task_id",
                "bundle_dir",
                "result_json",
                "stdout_log",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "o001",
                "codex",
                "cancel-async-tasks",
                "jobs/raw/o001",
                "jobs/raw/o001/missing.json",
                "jobs/raw/o001/stdout.log",
            ]
        )
        + "\n",
    )

    issues = validate_bundle_dir(bundle)
    assert any("references missing path" in issue.message for issue in issues)


def test_validate_study_bundle_requires_populated_study_columns(tmp_path: Path) -> None:
    bundle = _make_complete_bundle(tmp_path)
    _write_file(
        bundle / "summary.tsv",
        "\t".join(
            [
                "order",
                "condition",
                "task_id",
                "zero_touch_success",
                "human_interactions_after_assignment",
                "active_touch_minutes_after_assignment",
                "manual_commands",
                "manual_rescue",
                "result_json",
                "stdout_log",
                "stderr_log",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "1",
                "codex",
                "cancel-async-tasks",
                "",
                "0",
                "0.0",
                "0",
                "",
                "jobs/raw/o001/result.json",
                "jobs/raw/o001/stdout.log",
                "jobs/raw/o001/stderr.log",
            ]
        )
        + "\n",
    )

    issues = validate_bundle_dir(bundle)
    assert any("missing required study columns" in issue.message for issue in issues) or any(
        "study row" in issue.message for issue in issues
    )


def test_validate_study_bundle_rejects_zero_touch_contradiction(tmp_path: Path) -> None:
    bundle = _make_complete_bundle(tmp_path)
    _write_file(
        bundle / "summary.tsv",
        "\t".join(
            [
                "order",
                "condition",
                "task_id",
                "needs_human",
                "zero_touch_success",
                "human_interactions_after_assignment",
                "active_touch_minutes_after_assignment",
                "manual_commands",
                "manual_rescue",
                "intervention_severity",
                "result_json",
                "stdout_log",
                "stderr_log",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "1",
                "codex",
                "cancel-async-tasks",
                "False",
                "False",
                "0",
                "0.0",
                "0",
                "",
                "zero_touch",
                "jobs/raw/o001/result.json",
                "jobs/raw/o001/stdout.log",
                "jobs/raw/o001/stderr.log",
            ]
        )
        + "\n",
    )

    issues = validate_bundle_dir(bundle)
    assert any(
        "contradicts needs_human=False with zero_touch_success=False" in issue.message
        for issue in issues
    )


def test_validate_bundle_dir_allows_explicit_exemption(tmp_path: Path) -> None:
    bundle = _make_complete_bundle(tmp_path, with_exempt=True)
    assert validate_bundle_dir(bundle) == []


def test_validate_results_root_visits_all_top_level_dirs(tmp_path: Path) -> None:
    archive_root = tmp_path / "evidence"
    _make_complete_bundle(archive_root)

    incomplete = archive_root / "argus-skill-harbor"
    _write_file(incomplete / "skills" / "bounded-asyncio-task-runner.md")

    exempt = archive_root / "tb2-legacy-2026-05-10"
    _write_file(exempt / "EXEMPT.md", "legacy partial bundle\n")

    issues = validate_results_root(archive_root)
    assert any(
        issue.path == incomplete and issue.message == "missing required file: BUILD_INFO.md"
        for issue in issues
    )
    assert not any(issue.path == exempt for issue in issues)


def test_validate_current_archive_tree(tmp_path: Path) -> None:
    archive_root = Path("benchmarks/evidence")
    if not archive_root.exists():
        archive_root = tmp_path / "evidence"
        _make_complete_bundle(archive_root)
    issues = validate_results_root(archive_root)
    assert issues == []


def test_known_bugs_documents_current_exempt_result_bundles() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    docs = (repo_root / "docs" / "KNOWN_BUGS.md").read_text(encoding="utf-8")

    documented = {
        match.group(1).rstrip("/")
        for match in re.finditer(r"^- `([^`]+/?)`$", docs, re.MULTILINE)
        if match.group(1).startswith(("benchmarks/results/", "benchmarks/evidence/"))
    }
    completed = subprocess.run(
        ["git", "ls-files", "benchmarks/results", "benchmarks/evidence"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    tracked = completed.stdout.splitlines() if completed.returncode == 0 else []
    live = {
        str((repo_root / path).parent.relative_to(repo_root))
        for path in tracked
        if path.endswith("/EXEMPT.md")
        and path.startswith(("benchmarks/results/", "benchmarks/evidence/"))
    }

    assert documented == live
