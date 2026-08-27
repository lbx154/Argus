from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.literary_editor.stages import stage_completion_issues


def test_edit_completion_enforces_nonempty_and_must_keep(tmp_path: Path) -> None:
    assert stage_completion_issues("edit", tmp_path)
    editor = tmp_path / "editor"
    editor.mkdir()
    (editor / "source.txt").write_text("原文有一个关键句。", encoding="utf-8")
    (editor / "edited.txt").write_text("润色后仍有一个关键句。", encoding="utf-8")
    (editor / "edit_brief.json").write_text(
        json.dumps({"mode": "polish", "must_keep": ["关键句"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert stage_completion_issues("edit", tmp_path) == ()

    (editor / "edited.txt").write_text("删掉了必须保留的内容。", encoding="utf-8")
    assert stage_completion_issues("edit", tmp_path)
