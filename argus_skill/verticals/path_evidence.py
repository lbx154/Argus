"""Portable structural file-existence checks for vertical stage gates."""
from __future__ import annotations

import argparse
import fnmatch
import sys
from functools import lru_cache
from pathlib import Path, PurePosixPath


class PathEvidenceError(ValueError):
    """Raised when none of the declared artifact globs has a non-empty file."""


def _is_contained_nonempty_file(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return (
        resolved.is_relative_to(root)
        and resolved.is_file()
        and resolved.stat().st_size > 0
    )


def _casefold_glob_match(relative: PurePosixPath, pattern: str) -> bool:
    path_parts = relative.parts
    pattern_parts = PurePosixPath(pattern).parts

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], part)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def validate_any_file(
    project_root: Path,
    patterns: list[str],
    *,
    case_insensitive_patterns: list[str] | None = None,
) -> Path:
    """Return the first non-empty regular file matching a project-relative glob."""
    root = project_root.resolve()
    for pattern in patterns:
        candidate_pattern = str(pattern or "").strip()
        if not candidate_pattern or Path(candidate_pattern).is_absolute():
            continue
        for path in sorted(root.glob(candidate_pattern)):
            if _is_contained_nonempty_file(root, path):
                return path
    folded_patterns = [
        str(pattern or "").strip().lower()
        for pattern in (case_insensitive_patterns or [])
        if str(pattern or "").strip() and not Path(str(pattern)).is_absolute()
    ]
    for path in sorted(root.rglob("*")):
        if not _is_contained_nonempty_file(root, path):
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix().lower())
        if any(_casefold_glob_match(relative, pattern) for pattern in folded_patterns):
            return path
    raise PathEvidenceError(
        "no non-empty project file matches: "
        + ", ".join(
            repr(pattern)
            for pattern in [*patterns, *(case_insensitive_patterns or [])]
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus-path-evidence")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--iglob", action="append", default=[])
    args = parser.parse_args(argv)
    if not args.glob and not args.iglob:
        parser.error("at least one --glob or --iglob is required")
    root = Path(args.project_root).resolve()
    try:
        path = validate_any_file(
            root,
            list(args.glob),
            case_insensitive_patterns=list(args.iglob),
        )
    except PathEvidenceError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: structural artifact {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
