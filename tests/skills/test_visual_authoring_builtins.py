from pathlib import Path

import yaml

from argus.skills.builtins import iter_builtin_skill_texts

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


def test_robot_body_skill_ships_globally_and_reviewers_are_pointed_at_it(tmp_path) -> None:
    # A body or mechanism is judged by loading and seeing it, in any vertical,
    # so the skill is global and the Reviewer's role names the same checks.
    from argus.skills.builtins import seed_builtin_skills

    texts = dict(iter_builtin_skill_texts())
    relative = "engineer/robot-embodiment-urdf.md"
    front, body = _front_body_text(texts[relative])
    assert set(front) == {"name", "description"}
    for term in (
        "robot/", ".urdf", "closure.json", "previews/", "README.md", "yourdfpy",
        "positive definite", "relative path", ".glb",
    ):
        assert term in body, term
    assert "package://" in body  # named as the path form to avoid

    seed_builtin_skills(tmp_path)
    assert (tmp_path / relative).read_text(encoding="utf-8") == texts[relative]

    _front, reviewer = _front_body_text(texts["reviewer/argus-reviewer-role.md"])
    assert relative in reviewer and "## Visual results" in reviewer
    _front, planner = _front_body_text(texts["planner/dependency-aware-task-decomposition.md"])
    assert "acceptance check names that visual as the primary output" in planner
