"""The Goal Gate mission should be named after the work it has to finish.

Every one of twelve real runs on 2026-07-26 queued a first task called
"Complete and certify the current Goal Gate". On a single-stage vertical like
`software` that mission is the one that writes all the code, so an operator
watching the queue saw a task named after certification while the Engineer was
implementing from scratch.

Naming the stage also makes the deduplication signature stage-specific, which is
more correct: the gate task for `delivery` and the gate task for `submission`
are different work, not a repeat of the same one.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.supervisor._planning_cycle_helpers import goal_gate_task_title


def _project(tmp_path: Path, *, vertical: str, stage: str) -> Path:
    from argus_skill.skills.vertical_select import _state_path, persist_vertical

    persist_vertical(tmp_path, vertical)
    path = _state_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_the_title_names_the_stage(tmp_path: Path) -> None:
    root = _project(tmp_path, vertical="software", stage="delivery")

    assert goal_gate_task_title(root) == "Finish and certify the delivery stage"


def test_two_stages_produce_two_different_titles(tmp_path: Path) -> None:
    """So the deduplication signature does not merge unrelated gate work."""
    early = _project(tmp_path / "a", vertical="research", stage="research")
    late = _project(tmp_path / "b", vertical="research", stage="submission")

    assert goal_gate_task_title(early) != goal_gate_task_title(late)


def test_a_project_with_no_state_still_gets_a_usable_title(
    tmp_path: Path,
) -> None:
    """`current_stage` defaults rather than raising, so the title is still named.

    The generic fallback stays for the case where reading the stage raises, but
    it is not what an absent state file produces — measured, not assumed.
    """
    title = goal_gate_task_title(tmp_path / "nowhere")

    assert title.startswith("Finish and certify the ")


def test_a_stage_read_that_raises_falls_back_to_the_generic_title(
    monkeypatch, tmp_path: Path
) -> None:
    def _boom(_root):
        raise RuntimeError("pipeline state unreadable")

    monkeypatch.setattr("argus_skill.skills.stage_machine.current_stage", _boom)

    assert goal_gate_task_title(tmp_path) == (
        "Complete and certify the current Goal Gate"
    )


def test_the_planner_uses_it(tmp_path: Path) -> None:
    """Otherwise the helper is a nicer name nothing ever prints."""
    import inspect

    from argus_skill.life.supervisor import _planning_cycle_completion

    source = inspect.getsource(_planning_cycle_completion)
    assert "goal_gate_task_title(" in source
    assert 'title="Complete and certify the current Goal Gate"' not in source
