from __future__ import annotations

from pathlib import Path

from argus_skill.skills.role_library import render_skill_library_paths


class _SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def library_roots(self) -> list[Path]:
        return [self.root]


def test_skill_library_requires_live_probe_for_mutable_hardware_facts(
    tmp_path: Path,
) -> None:
    rendered = render_skill_library_paths(_SkillStore(tmp_path), role="engineer")

    assert "do not prove current availability" in rendered
    assert "Probe mutable runtime facts" in rendered
    assert "GPU models" in rendered
    assert "service health" in rendered
