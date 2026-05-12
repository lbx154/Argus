"""``argus-skill --skill-cleanse`` — retroactively scrub bad task_history.

Earlier versions of the supervisor concatenated a long "Memory context"
prelude with the live objective and passed the whole thing as the task
identifier into ``append_task_history``, which truncates at 200 chars.
Result: every existing skill on disk has 1-32 ``task_history`` entries
that all start with "### Memory context (non-authoritative)" and never
reach the actual objective. Useless as a recall signal for the matcher.

This helper rewrites every skill file under ``<skills_dir>`` to drop
those polluted entries. Idempotent — clean files are unchanged.
"""
from __future__ import annotations

from pathlib import Path

from ..skills.store import Skill, SkillStore, cleanse_task_history


def run_cleanse(
    skills_dir: Path, *, dry_run: bool = False
) -> int:
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        print("cleanse: no skill files found")
        return 0
    store = SkillStore(skills_dir=skills_dir)
    summaries = store.list_summaries()
    if not summaries:
        print("cleanse: no skill files found")
        return 0
    total_removed = 0
    touched = 0
    for s in summaries:
        path = s["path"]
        skill: Skill = store.load(path)
        removed = cleanse_task_history(skill)
        if removed > 0:
            total_removed += removed
            touched += 1
            print(
                f"  {Path(path).name}: dropped {removed} polluted "
                f"task_history entries"
            )
            if not dry_run:
                store.save(skill)
    suffix = " (dry-run)" if dry_run else ""
    print(
        f"cleanse: scanned {len(summaries)} skills; "
        f"rewrote {touched} files; dropped {total_removed} entries{suffix}"
    )
    return 0


__all__ = ["run_cleanse"]
