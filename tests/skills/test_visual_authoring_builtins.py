from pathlib import Path

import yaml

from argus.skills.builtins import seed_builtin_skills

ROOT = Path(__file__).resolve().parents[2] / "argus" / "builtin_skills"
VISUAL_SKILLS = {
    "engineer/presentation-master.md": ("ppt-master", "SKILL.md", "PPT_MASTER_ROOT"),
}


def _front_body(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def test_visual_authoring_skills_are_minimal_and_operational() -> None:
    for relative, required_terms in VISUAL_SKILLS.items():
        front, body = _front_body(ROOT / relative)
        assert set(front) == {"name", "description"}
        assert front["name"] and front["description"]
        for term in required_terms:
            assert term in body


def test_visual_authoring_skills_seed_into_runtime(tmp_path: Path) -> None:
    seeded = seed_builtin_skills(tmp_path)
    for relative in VISUAL_SKILLS:
        assert seeded[relative] is True
        assert (tmp_path / relative).is_file()


def test_seeding_preserves_existing_agent_document(tmp_path: Path) -> None:
    destination = tmp_path / "engineer" / "presentation-master.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("operator-authored Skill\n", encoding="utf-8")
    seeded = seed_builtin_skills(tmp_path)
    assert seeded["engineer/presentation-master.md"] is False
    assert destination.read_text(encoding="utf-8") == "operator-authored Skill\n"


def test_presentation_description_preserves_routing_guidance() -> None:
    presentation, presentation_body = _front_body(
        ROOT / "engineer" / "presentation-master.md"
    )
    assert "research figure policy comes from the active research vertical" in presentation["description"]
    assert "Method D" not in presentation["description"]
    assert "update_repo.py" in presentation_body
    assert '"${ARGUS_SKILL_PYTHON:-python3}"' in presentation_body
    assert "Do not call bare `python` or `python3`" in presentation_body
