from __future__ import annotations

import pytest

from argus.roles.prompts.engineer import build_mission_prompt

_GROUNDING = (
    "\n\n## Manager project grounding (advisory evidence)\n"
    "Architecture: gateway -> runner. Verification: focused tests."
)


def test_shared_manager_grounding_renders_once_for_distinct_task_wording() -> None:
    prompt = build_mission_prompt(
        task="Canonical execution objective." + _GROUNDING,
        original_request="Operator wording." + _GROUNDING,
        skill_text="",
        next_action=None,
    )

    assert prompt.count("Manager project grounding") == 1
    assert prompt.count("gateway -> runner") == 1
    assert "Operator wording." in prompt
    assert "Canonical execution objective." in prompt


@pytest.mark.parametrize("compact_team", [False, True])
@pytest.mark.parametrize("include_static", [False, True])
def test_reviewer_guidance_survives_every_engineer_prompt_path(
    compact_team: bool,
    include_static: bool,
) -> None:
    feedback = "Preserve search.py on disk and run it before finishing."
    operator_context = "Keep the work limited to the requested search."
    prompt = build_mission_prompt(
        task="Investigate the conjecture and provide a reproducible script.",
        skill_text="",
        next_action=feedback,
        compact_team=compact_team,
        include_static=include_static,
        operator_context=operator_context,
    )

    assert prompt.count(feedback) == 1
    assert prompt.count("## Reviewer guidance from prior round") == 1
    assert prompt.index(feedback) < prompt.index(operator_context)
