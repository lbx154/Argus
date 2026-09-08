"""Operator map notes reach the Planner through the current-reality digest.

A note pinned on an Atlas node is advice the Planner must actually see, or the
annotation feature is write-only. The digest renders the 5 most recent notes
as an ``operator_map_notes`` section — and a campaign without notes must pay
nothing for the feature: one existence probe, no file read, no rendered line.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus_skill.planner import PlannerConfig
from argus_skill.skills.vertical_select import persist_vertical


class _MissionRunner:
    pass


class _PlannerBackend:
    def run_exec(self, **kwargs):  # noqa: ANN003
        return RunnerResult(exit_code=0, agent_messages=[""])


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="keep optimizing",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=_PlannerBackend(),
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        open_ended=True,
    )
    return supervisor


def _write_notes(life: Path, texts: list[str], node_id: str = "task-a") -> None:
    rows = [
        {"id": f"n{i}", "node_id": node_id, "text": text, "author": "", "ts": float(i)}
        for i, text in enumerate(texts)
    ]
    (life / "map_notes.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_digest_renders_the_five_most_recent_notes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)
    _write_notes(life, [f"提示 {i}" for i in range(7)])

    note = supervisor._planner_current_reality_note()

    assert "- operator_map_notes:" in note
    assert "  - on task task-a: 提示 6" in note
    assert "提示 2" in note
    # Only the five most recent notes appear.
    assert "提示 1" not in note
    assert "提示 0" not in note


def test_note_text_is_clipped_and_redacted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)
    _write_notes(life, ["密钥 AKIAABCDEFGHIJKLMNOP " + "长" * 500])

    note = supervisor._planner_current_reality_note()

    assert "AKIAABCDEFGHIJKLMNOP" not in note
    assert "<REDACTED:aws-key>" in note
    note_line = next(
        line for line in note.splitlines() if line.startswith("  - on task task-a:")
    )
    assert len(note_line) < 260


def test_absent_notes_file_renders_nothing_and_reads_nothing(
    tmp_path: Path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)

    import argus_skill.life.memory as memory_module

    reads: list[Path] = []
    original = memory_module._read_jsonl_tail

    def counting_tail(path, n, **kwargs):
        reads.append(Path(path))
        return original(path, n, **kwargs)

    monkeypatch.setattr(memory_module, "_read_jsonl_tail", counting_tail)

    note = supervisor._planner_current_reality_note()

    assert "operator_map_notes" not in note
    assert not any(path.name == "map_notes.jsonl" for path in reads)


def test_unreadable_notes_file_never_breaks_the_digest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    supervisor = _supervisor(project, life)
    (life / "map_notes.jsonl").write_text("not json\n", encoding="utf-8")

    note = supervisor._planner_current_reality_note()

    assert "## Host current-reality digest" in note
    assert "operator_map_notes" not in note
