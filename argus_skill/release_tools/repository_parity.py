from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Iterable

PRIVATE_ONLY_PATTERNS = (
    "ARGUS_IMPRESSIVE_RESULTS.md",
    "ARGUS_IMPRESSIVE_RESULTS.zh-CN.md",
    "PRIVATE_TODO.md",
    "PRIVATE_TODO.zh-CN.md",
    "docs/RESEARCH_AGENCY_AND_VERIFICATION_TODO.md",
    "docs/evaluations/**",
    "technical_report/**",
    "tests/test_operator_output_examples.py",
)


def is_private_only(path: str, patterns: Iterable[str] = PRIVATE_ONLY_PATTERNS) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def unexpected_differences(
    paths: Iterable[str],
    patterns: Iterable[str] = PRIVATE_ONLY_PATTERNS,
) -> list[str]:
    return sorted({
        path.replace("\\", "/").strip()
        for path in paths
        if path.strip() and not is_private_only(path, patterns)
    })


def changed_paths(
    repo_root: Path,
    *,
    private_ref: str,
    public_ref: str,
) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "diff",
            "--name-only",
            "--no-renames",
            public_ref,
            private_ref,
            "--",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]
