"""Legacy command retained as a no-op after Skill metadata removal."""
from __future__ import annotations

from pathlib import Path


def run_skill_cleanse(skills_dir: Path, *, dry_run: bool = True) -> int:
    _ = (skills_dir, dry_run)
    print("cleanse: unnecessary; Skill files contain no runtime metadata")
    return 0


def cleanse_skills(skills_dir: Path) -> int:
    return run_skill_cleanse(skills_dir)


def run_cleanse(skills_dir: Path, *, dry_run: bool = True) -> int:
    return run_skill_cleanse(skills_dir, dry_run=dry_run)
