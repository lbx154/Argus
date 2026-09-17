"""The library event names the pages recall already showed the role, when the caller has them."""
from pathlib import Path

from argus.core.event_catalog import validate_event_envelope
from argus.skills.role_library import role_skill_libraries
from argus.skills.store import SkillStore


def _store(tmp_path: Path) -> SkillStore:
    root = tmp_path / "skills"
    (root / "engineer").mkdir(parents=True)
    (root / "engineer" / "example.md").write_text(
        "---\nname: Example\ndescription: Example guidance.\n---\n\nBODY\n", encoding="utf-8",
    )
    return SkillStore(root)


def test_recalled_paths_are_reported_without_changing_discovery(tmp_path: Path) -> None:
    events: list[dict] = []
    recalled = [tmp_path / "wiki" / "pages" / "lessons" / "a.md", str(tmp_path / "wiki" / "principles.md"),
                tmp_path / "wiki" / "pages" / "lessons" / "a.md"]

    result = role_skill_libraries(_store(tmp_path), role="engineer", on_event=events.append,
                                  recalled_paths=recalled)

    assert result.recalled_paths == [Path(recalled[0]), Path(recalled[1])]
    (event,) = events
    assert event["recalled_paths"] == [str(recalled[0]), str(recalled[1])]
    assert validate_event_envelope(event).valid
    assert "a.md" not in result.block and "principles" not in result.block


def test_recalled_paths_default_to_empty(tmp_path: Path) -> None:
    events: list[dict] = []
    result = role_skill_libraries(_store(tmp_path), role="engineer", on_event=events.append)
    assert result.recalled_paths == []
    assert events[0]["recalled_paths"] == []
