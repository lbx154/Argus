from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

TEST_RE = re.compile(r"(^|/)(__tests__|test|tests|spec|specs)(/|$)")
DOCS_RE = re.compile(r"(^|/)(docs?|documentation)(/|$)")
CONFIG_RE = re.compile(r"(^|/)(\.github/workflows|ci|config)(/|$)")
DOCS_BASENAME_PREFIXES = (
    "changelog",
    "code_of_conduct",
    "contributing",
    "readme",
    "security",
)
CONFIG_BASENAMES = {
    ".gitattributes",
    ".gitignore",
    ".mcp.json",
    "build.gradle",
    "composer.json",
    "config.json",
    "environment.yml",
    "gradle.properties",
    "package-lock.json",
    "package.json",
    "plugin.json",
    "pom.xml",
    "pyproject.toml",
    "release_manifest.json",
    "requirements.txt",
    "setup.cfg",
    "tox.ini",
    "yarn.lock",
}


def is_test_path(path: str) -> bool:
    value = path.lower()
    return bool(
        TEST_RE.search(value)
        or value.endswith(
            ("_test.py", "_tests.py", ".spec.js", ".spec.ts", ".test.js", ".test.ts")
        )
    )


def is_docs_path(path: str) -> bool:
    value = path.lower()
    basename = value.rsplit("/", 1)[-1]
    return bool(
        DOCS_RE.search(value)
        or basename.startswith(DOCS_BASENAME_PREFIXES)
    )


def is_config_path(path: str) -> bool:
    value = path.lower()
    basename = value.rsplit("/", 1)[-1]
    return bool(
        CONFIG_RE.search(value)
        or basename in CONFIG_BASENAMES
        or (basename.startswith("tsconfig") and basename.endswith(".json"))
        or basename.endswith(".config.json")
        or value.endswith(
            (".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".lock")
        )
    )


def _git(
    args: list[str],
    *,
    repo: Path | None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    return result


def patch_stats(
    base_sha: str,
    head_sha: str,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    merge_base = _git(
        ["git", "merge-base", base_sha, head_sha],
        repo=repo,
    ).stdout.strip()
    if not merge_base:
        raise RuntimeError("git merge-base returned an empty revision")

    diff = subprocess.run(
        ["git", "diff", "--numstat", "-z", "--find-renames", merge_base, head_sha],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if diff.returncode != 0:
        detail = os.fsdecode(diff.stderr).strip() or "unknown error"
        raise RuntimeError(f"git diff --numstat failed: {detail}")

    additions = deletions = 0
    files: list[str] = []
    renames: list[dict[str, str]] = []
    classification_paths: list[tuple[str, ...]] = []
    fields = diff.stdout.split(b"\0")
    index = 0
    while index < len(fields) and fields[index]:
        parts = fields[index].split(b"\t", 2)
        if len(parts) != 3:
            raise RuntimeError("git diff --numstat returned a malformed record")
        added_raw, deleted_raw, path_raw = parts
        added = os.fsdecode(added_raw)
        deleted = os.fsdecode(deleted_raw)
        if added != "-" and deleted != "-":
            additions += int(added)
            deletions += int(deleted)
        if path_raw:
            path = os.fsdecode(path_raw)
            files.append(path)
            classification_paths.append((path,))
            index += 1
            continue

        if index + 2 >= len(fields) or not fields[index + 1] or not fields[index + 2]:
            raise RuntimeError("git diff --numstat returned an incomplete rename record")
        old_path = os.fsdecode(fields[index + 1])
        new_path = os.fsdecode(fields[index + 2])
        files.append(new_path)
        renames.append({"old_path": old_path, "new_path": new_path})
        classification_paths.append((old_path, new_path))
        index += 3

    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base_sha": merge_base,
        "comparison": "merge_base_to_head",
        "additions": additions,
        "deletions": deletions,
        "total_churn": additions + deletions,
        "files": files,
        "renames": renames,
        "files_test_count": sum(
            any(is_test_path(path) for path in paths)
            for paths in classification_paths
        ),
        "files_docs_count": sum(
            any(is_docs_path(path) for path in paths)
            for paths in classification_paths
        ),
        "files_config_count": sum(
            any(is_config_path(path) for path in paths)
            for paths in classification_paths
        ),
    }
