from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

TEST_DIR_RE = re.compile(r"(^|/)(__tests__|test|tests|spec|specs)(/|$)")
TEST_BASENAME_RE = re.compile(
    r"^(?:"
    r"test_.+\.py|"
    r".+_tests?\.py|"
    r".+_test\.go|"
    r".+\.(?:test|spec)\.(?:[cm]?js|jsx|ts|tsx)"
    r")$"
)
DOCS_RE = re.compile(r"(^|/)(docs?|documentation)(/|$)")
DOCS_BASENAME_RE = re.compile(
    r"^(?:readme|changelog|contributing|security|code[_-]of[_-]conduct)"
    r"(?:[._-][a-z0-9]+(?:-[a-z0-9]+)*)*"
    r"\.(?:md|rst|adoc|txt)$"
)
CONFIG_DIR_RE = re.compile(
    r"(^|/)(\.github/workflows|\.circleci|ci|config|configs)(/|$)"
)
CONFIG_BASENAME_RE = re.compile(
    r"^(?:"
    r"tsconfig(?:\.[a-z0-9_-]+)*\.json|"
    r".+\.config\.(?:[cm]?js|ts|json|ya?ml)|"
    r"(?:docker-)?compose(?:\.[a-z0-9_-]+)?\.ya?ml|"
    r"dockerfile(?:\.[a-z0-9_-]+)?"
    r")$"
)
CONFIG_BASENAMES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".mcp.json",
    ".pre-commit-config.yaml",
    "build.gradle",
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "config.json",
    "dependabot.yml",
    "dependabot.yaml",
    "environment.yml",
    "go.mod",
    "go.sum",
    "gradle.properties",
    "mkdocs.yml",
    "mkdocs.yaml",
    "package-lock.json",
    "package.json",
    "plugin.json",
    "pnpm-lock.yaml",
    "pom.xml",
    "pyproject.toml",
    "release_manifest.json",
    "requirements.txt",
    "ruff.toml",
    "setup.cfg",
    "tox.ini",
    "yarn.lock",
}


def is_test_path(path: str) -> bool:
    value = path.lower()
    basename = value.rsplit("/", 1)[-1]
    return bool(TEST_DIR_RE.search(value) or TEST_BASENAME_RE.fullmatch(basename))


def is_docs_path(path: str) -> bool:
    value = path.lower()
    basename = value.rsplit("/", 1)[-1]
    return bool(DOCS_RE.search(value) or DOCS_BASENAME_RE.fullmatch(basename))


def is_config_path(path: str) -> bool:
    value = path.lower()
    basename = value.rsplit("/", 1)[-1]
    return bool(
        CONFIG_DIR_RE.search(value)
        or basename in CONFIG_BASENAMES
        or CONFIG_BASENAME_RE.fullmatch(basename)
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

    classifications = [
        (
            any(is_test_path(path) for path in paths),
            any(is_docs_path(path) for path in paths),
            any(is_config_path(path) for path in paths),
        )
        for paths in classification_paths
    ]

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
        "files_test_count": sum(item[0] for item in classifications),
        "files_docs_count": sum(item[1] for item in classifications),
        "files_config_count": sum(item[2] for item in classifications),
        "files_unknown_count": sum(not any(item) for item in classifications),
    }
