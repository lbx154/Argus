from __future__ import annotations

import pytest

from scripts.release_beta import (
    expected_release_assets,
    expected_versions,
    project_base_version,
    release_tag,
    select_new_run,
    validate_version,
    version_from_commit,
)


def test_validate_version_accepts_only_commit_betas() -> None:
    version = "0.1.1-beta.g0123456789ab"
    assert validate_version(version) == version
    for invalid in ("0.1.1", "0.1.1-beta.3", "0.1.1-beta.gshort"):
        with pytest.raises(ValueError):
            validate_version(invalid)


def test_version_is_derived_from_base_and_full_commit(tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.1.1"\n', encoding="utf-8")
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert project_base_version(pyproject) == "0.1.1"
    assert version_from_commit("0.1.1", sha) == "0.1.1-beta.g0123456789ab"


def test_expected_versions_match_single_package_variants() -> None:
    version = "0.1.1-beta.g0123456789ab"
    assert expected_versions(version) == (
        f"{version}-linux-x64",
        f"{version}-win32-x64",
        version,
    )


def test_github_release_contract_uses_the_same_version() -> None:
    version = "0.1.1-beta.g0123456789ab"
    assert release_tag(version) == f"v{version}"
    assert expected_release_assets(version) == {
        f"argus-{version}-linux-x64",
        f"argus-{version}-linux-x64.sha256",
        f"argus-{version}-win32-x64.exe",
        f"argus-{version}-win32-x64.exe.sha256",
        "THIRD_PARTY_NOTICES-linux.txt",
        "THIRD_PARTY_NOTICES-windows.txt",
        "release-metadata.json",
    }


def test_select_new_run_ignores_old_and_other_commit_runs() -> None:
    version = "0.1.1-beta.g0123456789ab"
    sha = "0123456789abcdef0123456789abcdef01234567"
    title = f"Argus {version} · {sha}"
    rows = [
        {
            "databaseId": 10,
            "displayTitle": title,
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 11,
            "displayTitle": "Argus other · other",
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 12,
            "displayTitle": title,
            "headBranch": "main",
            "event": "workflow_dispatch",
        },
        {
            "databaseId": 13,
            "displayTitle": title,
            "headBranch": "main",
            "event": "push",
        },
    ]
    assert select_new_run(rows, {10}, version, sha) == rows[2]
    assert select_new_run(rows, {10, 12}, version, sha) is None
