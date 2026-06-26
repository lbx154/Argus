from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

SKILL_MD = (
    "## Title\nExisting reusable playbook\n\n"
    "## Description\nA reusable playbook for testing matcher accounting.\n\n"
    "## Category\naccounting\n\n"
    "## When to use\n- only when explicitly matched\n\n"
    "## When NOT to use\n- unrelated tasks\n\n"
    "## How to solve\n- Do the task.\n\n"
    "## Examples\n- task -> done\n\n"
    "## Response shape\n- concise\n"
)


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Work completed.",
        "next_action": "No further action.",
        "round_summary_markdown": "# Review\n\n- done\n",
        "completion_summary_markdown": "Done.",
    })


def test_skill_loop_emits_cost_bearing_skill_event(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    store = SkillStore(skills_dir)
    store.save_distilled(
        task_description="seed",
        raw_distill_output=SKILL_MD,
    )
    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            message='{"matched": []}',
            input_tokens=1234,
            cached_input_tokens=234,
            output_tokens=56,
        ),
    )
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    events: list[dict] = []

    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="gpt-5.5",
            matcher_model="gpt-5.5-mini",
            max_rounds=1,
        ),
        on_event=events.append,
    )

    outcome = loop.run("novel task that should miss", workdir=tmp_path)

    assert outcome.successful
    cost_events = [e for e in events if e.get("type") == "skill.cost.completed"]
    assert len(cost_events) == 1
    event = cost_events[0]
    assert event["agent_layer"] == "scientist"
    assert event["matcher_model"] == "gpt-5.5-mini"
    assert event["distiller_model"] == "gpt-5.5"
    assert event["matcher"]["input_tokens"] == 1234
    assert event["matcher"]["cached_input_tokens"] == 234
    assert event["matcher"]["output_tokens"] == 56
    assert event["distiller"]["input_tokens"] == 0
    assert event["input_tokens"] == 1234
    assert event["cached_input_tokens"] == 234
    assert event["output_tokens"] == 56
