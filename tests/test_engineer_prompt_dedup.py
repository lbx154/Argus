from __future__ import annotations

from argus_skill.roles.prompts.engineer import build_mission_prompt

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


def test_engineer_prompt_forbids_repeated_checks_and_unbounded_tool_loops() -> None:
    prompt = build_mission_prompt(
        task="Audit the repository once.",
        skill_text="",
        next_action=None,
    )

    assert "Never repeat unchanged checks/reads" in prompt
    assert "never exceed 24" in prompt


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
    assert "## Engineer service" in prompt
    assert "## Engineer receipt" in prompt
    assert "/skills/engineer" in prompt
    assert "## Original operator request" not in prompt
    assert "FULL_VERTICAL_BANNER_MUST_NOT_REPEAT" not in prompt
    assert "## Shared project Wiki" not in prompt
    assert len(prompt) < 3_500
