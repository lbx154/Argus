from __future__ import annotations

import re
import subprocess
from typing import Any


TEST_RE = re.compile(r"(^|/)(__tests__|test|tests|spec|specs)(/|$)")
DOCS_RE = re.compile(r"(^|/)(docs?|documentation)(/|$)")
CONFIG_RE = re.compile(r"(^|/)(\.github/workflows|ci|config)(/|$)")
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
    return bool(
        "readme" in value
        or DOCS_RE.search(value)
        or value.endswith((".md", ".rst", ".adoc", ".txt"))
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


def patch_stats(base_sha: str, head_sha: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "diff", "--numstat", base_sha, head_sha],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")

    additions = deletions = 0
    files: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if added != "-" and deleted != "-":
            additions += int(added)
            deletions += int(deleted)
        files.append(path)

    return {
        "additions": additions,
        "deletions": deletions,
        "total_churn": additions + deletions,
        "files": files,
        "files_test_count": sum(is_test_path(path) for path in files),
        "files_docs_count": sum(is_docs_path(path) for path in files),
        "files_config_count": sum(is_config_path(path) for path in files),
    }
