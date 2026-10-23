from __future__ import annotations

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.roles.prompts.engineer import build_mission_prompt
from tests.hand_off import hand_off

_GROUNDING = (
    "\n\n## Manager project grounding (advisory evidence)\n"
    "Architecture: gateway -> runner. Verification: focused tests."
)


def test_identical_original_and_current_task_render_once() -> None:
    marker = "TOKEN_EFFICIENCY_OBJECTIVE_42"
    task = f"Audit {marker}."

    prompt = build_mission_prompt(
        task=task,
        original_request=task,
        skill_text="",
        next_action=None,
    )

    assert prompt.count(marker) == 1
    assert "## Original operator request" not in prompt


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


def test_engineer_prompt_leaves_working_methods_to_the_backend() -> None:
    prompt = build_mission_prompt(
        task="Audit the repository once.",
        skill_text="",
        next_action=None,
    )

    for ban in (
        "Never repeat unchanged", "compile-only ceremony", "add no hashes",
        "initialize Git", "subagents", "never exceed 24",
    ):
        assert ban not in prompt


def _without_host_shell_guidance(prompt: str) -> str:
    """Drop native-Windows shell guidance, which is host-specific by design."""
    from argus.roles.task_contract import (
        NATIVE_WINDOWS_SHELL_CONTRACT,
        NATIVE_WINDOWS_SHELL_SUMMARY,
    )

    for text in (NATIVE_WINDOWS_SHELL_CONTRACT, NATIVE_WINDOWS_SHELL_SUMMARY):
        prompt = prompt.replace(text, "")
    return prompt


def test_direct_team_prompt_uses_one_mission_contract() -> None:
    marker = "DIRECT_TEAM_CONTRACT_17"
    prompt = build_mission_prompt(
        task=f"## Mission contract\nDeliver {marker}.\n\nAcceptance:\ncheck it once",
        original_request=f"Long original request containing {marker}.",
        skill_text="## Skill libraries (on-demand)\n- `/skills/engineer`",
        role_banner="FULL_VERTICAL_BANNER_MUST_NOT_REPEAT",
        next_action=None,
        compact_team=True,
    )

    assert prompt.count(marker) == 1
    assert "You are the Argus Engineer" in prompt
    assert "/skills/engineer" in prompt
    assert "## Original operator request" not in prompt
    assert prompt.count("FULL_VERTICAL_BANNER_MUST_NOT_REPEAT") == 1
    assert "## Shared project Wiki" not in prompt
    assert len(_without_host_shell_guidance(prompt)) < 4_500


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


def test_direct_fresh_retry_forwards_reviewer_action_through_skill_loop(tmp_path) -> None:
    feedback = "Keep the requested search.py file on disk after verification."
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="first result"))
    backend.queue("reviewer", CannedResponse(action=hand_off("engineer", "search.py is missing", feedback)))
    backend.queue("engineer-r2", CannedResponse(message="corrected result"))
    backend.queue("reviewer", CannedResponse(action=hand_off("done", 'requested files present')))
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="fixture", reviewer_model="fixture",
            workflow_mode="direct", active_vertical="software",
            max_rounds=2,
            require_independent_review=True, require_post_task_learning=False,
            wiki_enabled=False, auto_init_wiki=False,
        ),
    )

    outcome = loop.run("Search the finite range and provide the requested script.", workdir=tmp_path)

    assert outcome.successful
    second_prompt = next(prompt for label, prompt, _ in backend.history if label == "engineer-r2")
    assert second_prompt.count(feedback) == 1
