from __future__ import annotations

from types import SimpleNamespace

from argus_skill.skills.evolution import (
    collect_skill_ops,
    evolve_skills_after_mission,
)


def _round(*ops):
    return SimpleNamespace(review=SimpleNamespace(skill_ops=list(ops)))


def test_collect_skill_ops_deduplicates_repeated_reviewer_proposals() -> None:
    op = {"op": "update", "name": "retry", "content": "full revised skill"}

    assert collect_skill_ops([_round(op), _round(dict(op))]) == [op]


def test_skill_evolution_applies_ops_and_emits_summary(tmp_path) -> None:
    class _Router:
        def __init__(self) -> None:
            self.ops = []

        def apply_ops(self, ops, *, task, on_event=None):
            self.ops = list(ops)
            return {"created": 1, "updated": 0, "archived": 0, "rejected": 0}

    router = _Router()
    events = []
    op = {"op": "create", "content": "new reusable skill"}

    summary = evolve_skills_after_mission(
        skill_store=SimpleNamespace(skills_dir=tmp_path),
        skill_router=router,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        rounds=[_round(op), _round(dict(op))],
        task="task",
        apply_ops_enabled=True,
        auto_compact_enabled=False,
        fallback_skills_dir=tmp_path,
        on_event=events.append,
    )

    assert router.ops == [op]
    assert summary["ops_proposed"] == 1
    assert summary["created"] == 1
    assert summary["project_skill_dir"] == str(tmp_path)
    assert events[-1]["type"] == "skill.evolution.completed"


def test_mission_compaction_never_mutates_shared_global_layer(
    tmp_path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "project-skills"
    global_dir = tmp_path / "global-skills"
    compacted = []

    monkeypatch.setattr(
        "argus_skill.skills.compaction.auto_compact_skills",
        lambda skills_dir, **kwargs: (
            compacted.append(skills_dir)
            or {"clusters": 0, "archived": 0, "errors": 0}
        ),
    )
    layered = SimpleNamespace(
        project=SimpleNamespace(skills_dir=project_dir),
        global_=SimpleNamespace(skills_dir=global_dir),
    )

    evolve_skills_after_mission(
        skill_store=layered,
        skill_router=SimpleNamespace(apply_ops=lambda *args, **kwargs: {}),
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
        rounds=[],
        task="task",
        apply_ops_enabled=False,
        auto_compact_enabled=True,
        fallback_skills_dir=tmp_path / "fallback",
    )

    assert compacted == [project_dir]
