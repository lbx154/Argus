from __future__ import annotations

import pytest

from scripts.release_beta import expected_versions, select_new_run, validate_version


def test_validate_version_accepts_only_numbered_betas() -> None:
    assert validate_version("0.1.1-beta.3") == "0.1.1-beta.3"
    for invalid in ("0.1.1", "0.1.1-beta.latest", "01.1.1-beta.3"):
        with pytest.raises(ValueError):
            validate_version(invalid)


def test_expected_versions_match_single_package_variants() -> None:
    assert expected_versions("0.1.1-beta.3") == (
        "0.1.1-beta.3-linux-x64",
        "0.1.1-beta.3-win32-x64",
        "0.1.1-beta.3",
    )


def test_select_new_run_ignores_old_and_other_commit_runs() -> None:
    rows = [
        {
            "databaseId": 10,
            "headSha": "current",
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 11,
            "headSha": "other",
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 12,
            "headSha": "current",
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 13,
            "headSha": "current",
            "headBranch": "main",
            "event": "push",
        },
    ]
    assert select_new_run(rows, {10}, "current") == rows[2]
    assert select_new_run(rows, {10, 12}, "current") is None
