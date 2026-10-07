from pathlib import Path

import yaml

from argus.skills.builtins import iter_builtin_skill_texts, seed_builtin_skills

RESEARCH_ROOT = (
    Path(__file__).resolve().parents[2]
    / "argus"
    / "verticals"
    / "research"
    / "skills"
)


def _front_body_text(text: str) -> tuple[dict, str]:
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def _front_body(path: Path) -> tuple[dict, str]:
    return _front_body_text(path.read_text(encoding="utf-8"))


def test_presentation_toolkit_notes_live_in_one_global_skill() -> None:
    # Editable slides serve every kind of project, so the toolkit notes stay in
    # the global library once and the research figure studio points at them.
    texts = dict(iter_builtin_skill_texts())
    front, body = _front_body_text(texts["engineer/presentation-master.md"])
    assert set(front) == {"name", "description"}
    for term in ("ppt-master", "update_repo.py", '"${ARGUS_SKILL_PYTHON:-python3}"', "+mn-lt"):
        assert term in body
    assert "Do not call bare `python` or `python3`" in body
    assert "Method D" in body and "paper_charts" in body

    _front, studio = _front_body(RESEARCH_ROOT / "engineer" / "paper-framework-figure-studio.md")
    assert "engineer/presentation-master.md" in studio
    for duplicated in ("update_repo.py", "Do not call bare", "+mn-lt"):
        assert duplicated not in studio, duplicated


def test_seeding_preserves_existing_agent_document(tmp_path: Path) -> None:
    destination = tmp_path / "engineer" / "pdf-chat.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("operator-authored Skill\n", encoding="utf-8")
    seeded = seed_builtin_skills(tmp_path)
    assert seeded["engineer/pdf-chat.md"] is False
    assert destination.read_text(encoding="utf-8") == "operator-authored Skill\n"
