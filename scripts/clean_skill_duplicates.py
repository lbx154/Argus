#!/usr/bin/env python3
"""Archive exact semantic skill duplicates without any LLM judgement.

Only files with identical role/name/description/category/body are grouped.
Source-tree cleanup moves UNTRACKED duplicates only; tracked files are never
changed. Every move is reversible and recorded in a JSON manifest.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import uuid
from collections import defaultdict
from pathlib import Path

from argus_skill.skills.store import Skill

ROLES = {"engineer", "reviewer", "planner", "manager"}


def _norm(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def _role(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    return parts[0] if len(parts) > 1 and parts[0] in ROLES else "general"


def _skills(root: Path) -> list[tuple[Path, Skill]]:
    rows: list[tuple[Path, Skill]] = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if "_archive" in relative.parts or any(part.startswith(".") for part in relative.parts):
            continue
        try:
            skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, UnicodeError):
            continue
        if skill.name.strip():
            rows.append((path, skill))
    return rows


def _semantic_key(root: Path, path: Path, skill: Skill) -> tuple[str, ...]:
    return (
        _role(root, path),
        _norm(skill.name),
        _norm(skill.description),
        _norm(skill.category),
        _norm(skill.content),
    )


def _payload_key(skill: Skill) -> tuple[str, ...]:
    return (
        _norm(skill.name),
        _norm(skill.description),
        _norm(skill.category),
        _norm(skill.content),
    )


def _numbered(path: Path) -> bool:
    return bool(re.search(r"-\d+$", path.stem))


def _runtime_keep_key(row: tuple[Path, Skill]) -> tuple:
    path, skill = row
    return (
        int(bool(skill.protected)),
        int(not skill.provisional),
        int(skill.successful_reuses),
        len(skill.task_history),
        int(skill.version),
        int(not _numbered(path)),
        -len(path.name),
        str(path),
    )


def _source_keep_key(row: tuple[Path, Skill], tracked: set[str]) -> tuple:
    path, _skill = row
    return (
        int(str(path) in tracked),
        int(not _numbered(path)),
        -len(path.name),
        str(path),
    )


def _duplicates(
    root: Path,
    *,
    tracked: set[str] | None = None,
) -> list[Path]:
    groups: dict[tuple[str, ...], list[tuple[Path, Skill]]] = defaultdict(list)
    for path, skill in _skills(root):
        groups[_semantic_key(root, path, skill)].append((path, skill))
    out: list[Path] = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        if tracked is None:
            keep = max(rows, key=_runtime_keep_key)
        else:
            keep = max(rows, key=lambda row: _source_keep_key(row, tracked))
        for path, _skill in rows:
            if path == keep[0]:
                continue
            if tracked is not None and str(path) in tracked:
                continue
            out.append(path)
    return sorted(out)


def _runtime_name_collisions(root: Path) -> list[Path]:
    groups: dict[tuple[str, str], list[tuple[Path, Skill]]] = defaultdict(list)
    for path, skill in _skills(root):
        groups[(_role(root, path), _norm(skill.name))].append((path, skill))
    out: list[Path] = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        keep = max(rows, key=_runtime_keep_key)
        out.extend(path for path, _skill in rows if path != keep[0])
    return sorted(out)


def _tracked(repo: Path) -> set[str]:
    output = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files"], text=True
    )
    return {str(repo / line) for line in output.splitlines() if line}


def _untracked_source_skills(repo: Path, source_roots: list[Path]) -> list[Path]:
    relative_roots = [str(root.relative_to(repo)) for root in source_roots if root.is_dir()]
    if not relative_roots:
        return []
    output = subprocess.check_output(
        [
            "git", "-C", str(repo), "status", "--porcelain",
            "--untracked-files=all", "--", *relative_roots,
        ],
        text=True,
    )
    out: list[Path] = []
    for line in output.splitlines():
        if not line.startswith("?? "):
            continue
        path = repo / line[3:]
        if path.suffix == ".md" and path.is_file():
            out.append(path)
    return sorted(out)


def _move(path: Path, target_root: Path, relative: Path) -> Path:
    target = target_root / relative
    if target.exists():
        target = target.with_name(f"{target.stem}.{uuid.uuid4().hex[:8]}{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--archive-untracked-source",
        action="store_true",
        help="also archive every unreviewed/untracked source-tree skill",
    )
    parser.add_argument(
        "--archive-runtime-name-collisions",
        action="store_true",
        help="archive ambiguous same-role/same-name runtime files",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--runtime", type=Path,
        default=Path.home() / ".argus-skill" / "skills",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    runtime = args.runtime.expanduser().resolve()
    source_roots = [repo / "argus_skill" / "builtin_skills"]
    source_roots.extend(sorted((repo / "argus_skill" / "verticals").glob("*/skills")))
    tracked = _tracked(repo)

    runtime_dupes = _duplicates(runtime)
    runtime_name_collisions = (
        _runtime_name_collisions(runtime)
        if args.archive_runtime_name_collisions
        else []
    )
    source_dupes: list[tuple[Path, Path]] = []
    for root in source_roots:
        if root.is_dir():
            source_dupes.extend((root, path) for path in _duplicates(root, tracked=tracked))
    untracked_source: list[Path] = []
    if args.archive_untracked_source:
        # Archive only source promotions that still have an identical runtime
        # copy. A source-only custom vertical skill may be active work and is
        # never safe to infer away merely because it is untracked.
        runtime_payloads = {_payload_key(skill) for _path, skill in _skills(runtime)}
        for path in _untracked_source_skills(repo, source_roots):
            try:
                skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))
            except (OSError, UnicodeError):
                continue
            if _payload_key(skill) in runtime_payloads:
                untracked_source.append(path)

    print(f"runtime exact duplicates: {len(runtime_dupes)}")
    if args.archive_runtime_name_collisions:
        print(f"runtime ambiguous name collisions: {len(runtime_name_collisions)}")
    print(f"source untracked exact duplicates: {len(source_dupes)}")
    if args.archive_untracked_source:
        print(f"source unreviewed/untracked skills: {len(untracked_source)}")
    if not args.apply:
        print("dry-run; pass --apply to archive these files")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    runtime_archive = runtime / "_archive" / f"exact-dedupe-{stamp}"
    source_archive = Path.home() / ".argus-skill" / "source_skill_archive" / stamp
    moves: list[dict[str, str]] = []
    for path in runtime_dupes:
        target = _move(path, runtime_archive, path.relative_to(runtime))
        moves.append({"kind": "runtime", "from": str(path), "to": str(target)})
    already_moved = {item["from"] for item in moves}
    for path in runtime_name_collisions:
        if str(path) in already_moved or not path.exists():
            continue
        target = _move(path, runtime_archive, path.relative_to(runtime))
        moves.append({"kind": "runtime-name-collision", "from": str(path), "to": str(target)})
    for root, path in source_dupes:
        relative = path.relative_to(repo)
        target = _move(path, source_archive, relative)
        moves.append({"kind": "source", "from": str(path), "to": str(target)})
    already_moved = {item["from"] for item in moves}
    for path in untracked_source:
        if str(path) in already_moved or not path.exists():
            continue
        relative = path.relative_to(repo)
        target = _move(path, source_archive, relative)
        moves.append({"kind": "source-unreviewed", "from": str(path), "to": str(target)})

    manifest = Path.home() / ".argus-skill" / "cleanup_manifests" / f"{stamp}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"created_at": stamp, "moves": moves}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"archived {len(moves)} files; manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
