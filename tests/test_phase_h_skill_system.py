import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend


def _done_review() -> str:
    return json.dumps(
        {
            "status": "done",
            "reason": "Work completed.",
            "next_action": "No further action.",
            "round_summary_markdown": "# Review\n\n- done\n",
            "completion_summary_markdown": "Done.",
        }
    )


def test_skill_loop_supplies_library_path_without_matcher_or_cost_event(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "example.md").write_text(
        "---\nname: Example\ndescription: Example guidance.\n---\n\n"
        "# Example\n\nPRIVATE SKILL BODY\n",
        encoding="utf-8",
    )
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    events: list[dict] = []

    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(engineer_model="gpt-5.5", max_rounds=1),
        on_event=events.append,
    )
    outcome = loop.run("complete the task", workdir=tmp_path)

    assert outcome.successful
    assert all(label != "matcher" for label, _prompt, _opts in backend.history)
    engineer_prompt = next(
        prompt for label, prompt, _opts in backend.history if label == "engineer-r1"
    )
    assert str(skills_dir.resolve()) in engineer_prompt
    assert "PRIVATE SKILL BODY" not in engineer_prompt
    assert not [event for event in events if event.get("type") == "skill.cost.completed"]
