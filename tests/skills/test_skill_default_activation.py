"""Skills are active by default; trajectory evidence drives later evolution."""
from __future__ import annotations

from pathlib import Path

from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import SkillStore


def _skill(name: str, description: str = "A reusable capability.") -> str:
    return (
        f"## Title\n{name}\n\n"
        f"## Description\n{description}\n\n"
        "## Body\nDo the relevant operation.\n"
    )


def test_create_is_immediately_active_without_duplicate_judge(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.save_distilled(
        task_description="seed",
        raw_distill_output=_skill("Existing Skill", "Same capability wording."),
    )

    router = SkillRouter(skill_store=store)
    counts = router.apply_ops(
        [{
            "op": "create",
            "content": _skill("Another Skill", "Same capability wording."),
        }],
        task="new task",
    )

    assert counts == {"created": 1, "updated": 0, "archived": 0, "rejected": 0}
    assert {row["name"] for row in store.list_summaries()} == {
        "Existing Skill", "Another Skill",
    }


def test_compact_structural_skill_has_no_minimum_length_gate(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    counts = SkillRouter(skill_store=store).apply_ops(
        [{"op": "create", "content": _skill("Compact Skill", "Do one thing.")}],
        task="compact task",
    )

    assert counts["created"] == 1
    assert store.list_summaries()[0]["name"] == "Compact Skill"


def test_update_preserves_previous_version_for_rollback(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    skill = store.save_distilled(
        task_description="seed",
        raw_distill_output=_skill("Versioned Skill", "Version one."),
    )
    assert skill is not None

    updated = store.update_skill_content(
        skill,
        _skill("Versioned Skill", "Version two."),
        task_desc="revise it",
    )

    assert updated is not None and updated.version == 2
    history = Path(updated.path).parent / "_history" / updated.skill_id / "v1.md"
    assert history.is_file()
    assert "Version one." in history.read_text(encoding="utf-8")
    assert not any("_history" in row["path"] for row in store.list_summaries())
