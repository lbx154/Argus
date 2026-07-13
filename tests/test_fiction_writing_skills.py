"""Every fiction_writing skill markdown must satisfy Argus's skill contract:
parseable frontmatter (name/description/category) + the required
`## When to use` and `## How to solve` body sections with real content.
Deterministic, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

import argus_skill.verticals.fiction_writing as fw
from argus_skill.skills.store import Skill

_SKILLS_DIR = Path(fw.__file__).resolve().parent / "skills"
_SKILL_FILES = sorted(_SKILLS_DIR.rglob("*.md"))


def test_skill_files_exist():
    names = {p.name for p in _SKILL_FILES}
    assert {
        "creative-brief-and-style-profile.md",
        "story-and-chapter-planning.md",
        "chapter-drafting-and-continuation.md",
        "story-state-update.md",
        "targeted-fiction-revision.md",
        "continuity-style-and-plot-review.md",
    } <= names


@pytest.mark.parametrize("path", _SKILL_FILES, ids=lambda p: p.name)
def test_skill_meets_argus_contract(path: Path):
    skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))
    assert skill.name.strip(), f"{path.name}: missing name"
    assert skill.description.strip(), f"{path.name}: missing description"
    assert skill.category.strip(), f"{path.name}: missing category"
    body = skill.content.lower()
    assert "when to use" in body, f"{path.name}: missing '## When to use'"
    assert "how to solve" in body, f"{path.name}: missing '## How to solve'"
    # SkillRouter enforces a non-trivial body (>=120 chars).
    assert len(skill.content) >= 120, f"{path.name}: body too short"
