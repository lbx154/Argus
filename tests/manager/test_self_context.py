from pathlib import Path

import pytest

from argus.core.pipeline_state import read_pipeline_state
from argus.manager import Manager
from argus.manager.self_context import self_skill_context
from argus.skills.layered import LayeredSkillStore
from argus.skills.store import SkillStore
from argus.skills.vertical_select import persist_vertical
from argus.verticals._data_domain import (
    load_data_domain,
    promote_data_domain,
    write_data_domain,
)


def make_manager(tmp_path: Path) -> Manager:
    state = tmp_path / "projects" / "current"
    return Manager(
        state, execution_workdir=tmp_path / "workspace", learned_vertical_root=tmp_path,
        skill_store=LayeredSkillStore(project_dir=state / "skills", global_dir=tmp_path / "skills"),
        memory_maintenance_enabled=False,
    )


def test_select_and_clear_vertical_without_mutating_active_campaign(tmp_path):
    manager = make_manager(tmp_path)
    persist_vertical(manager.project_root, "research", workflow_mode="staged")
    before = read_pipeline_state(manager.project_root)
    original_store = manager.skill_store
    events = []
    matched = self_skill_context(manager, vertical="software", on_event=events.append)
    assert "SOFTWARE VERTICAL" in matched.block
    assert "do not create a Team" in matched.block
    shared = tmp_path / "skills" / "_shared_verticals" / "software"
    assert shared / "engineer" in matched.native_paths
    assert list((shared / "engineer").glob("*.md")), "Bundled skills must be available on first SELF use"
    assert events[0]["vertical"] == "software"
    unmatched = self_skill_context(manager, vertical="")
    assert shared not in unmatched.library_roots
    assert "SOFTWARE VERTICAL" not in unmatched.block
    assert not any("_shared_verticals" in str(path) for path in unmatched.library_roots)
    assert manager.skill_store is original_store
    assert read_pipeline_state(manager.project_root) == before


def test_self_materializes_shared_learned_vertical_and_preserves_learned_skills(tmp_path):
    manager = make_manager(tmp_path)
    source = tmp_path / "projects" / "author"
    write_data_domain(source, "calendar_checks", stages=["execute"], created_by="manager",
                      purpose="Validate calendar dates", status="candidate",
                      role_banner={"engineer": "Check actual calendar validity.",
                                   "reviewer": "Independently verify calendar sources."})
    assert promote_data_domain(source, tmp_path, "calendar_checks", review_reason="fixture verification")
    skill = tmp_path / "skills/_shared_verticals/calendar_checks/engineer/calendar.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: calendar\ndescription: Validate calendar dates\n---\nKeep learned rules.\n")
    before = skill.read_bytes()
    libraries = self_skill_context(manager, vertical="calendar_checks")
    assert "Check actual calendar validity." in libraries.block
    assert skill.parent in libraries.native_paths
    assert load_data_domain("calendar_checks", manager.project_root).status == "formal"
    assert read_pipeline_state(manager.project_root) == {}
    assert skill.read_bytes() == before
    reviewed = self_skill_context(manager, vertical="calendar_checks", role="reviewer")
    assert "Independently verify calendar sources." in reviewed.block


def test_persisted_state_uses_session_root_and_refreshes_cached_manager(tmp_path):
    manager = make_manager(tmp_path)
    persist_vertical(manager.project_root, "software", workflow_mode="direct")
    assert "SOFTWARE VERTICAL" in self_skill_context(manager).block
    persist_vertical(manager.project_root, "research", workflow_mode="direct")
    updated = self_skill_context(manager)
    assert "single-agent task: research" in updated.block
    assert "single-agent task: software" not in updated.block


def test_plain_store_supports_vertical_without_losing_global_skills(tmp_path):
    manager = make_manager(tmp_path)
    manager.skill_store = SkillStore(tmp_path / "plain")
    libraries = self_skill_context(manager, vertical="software")
    assert tmp_path / "plain" in libraries.library_roots
    assert tmp_path / "plain/_shared_verticals/software/engineer" in libraries.native_paths


@pytest.mark.parametrize("name", ["../escape", "unknown_vertical", "private_candidate"])
def test_unavailable_selection_never_falls_back_or_writes_pipeline(tmp_path, name):
    manager = make_manager(tmp_path)
    write_data_domain(tmp_path / "projects/other", "private_candidate", stages=["execute"],
                      status="candidate", created_by="manager")
    with pytest.raises(ValueError, match="Unavailable SELF vertical"):
        self_skill_context(manager, vertical=name)
    assert read_pipeline_state(manager.project_root) == {}
