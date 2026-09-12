"""Each role's per-round prompt keeps its stable text as a byte-identical prefix.

The provider caches the prompt prefix across calls. Everything that changes
between the rounds of one mission — checkpoint text, journal, counters,
live vertical facts, operator steering — must therefore follow everything
that does not. Each test builds a prompt twice with different volatile inputs
and asserts the common prefix reaches the end of the stable block, located
by a marker in the text rather than by a magic number.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from argus_skill.core.model_visible_text import MODEL_INTEGRITY_BOUNDARY
from argus_skill.roles.prompts import engineer as engineer_prompts
from argus_skill.roles.prompts.engineer import (
    assemble_round_prompt,
    build_mission_prompt,
)
from argus_skill.roles.prompts.manager import (
    assemble_manager_prompt,
    build_stage_decision_prompt,
)
from argus_skill.roles.prompts.planner import (
    build_continuous_prompt,
    build_continuous_resume_prompt,
)
from argus_skill.roles.prompts.reviewer import render_reviewer_prompt
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals.research import prompt_policy


def _lcp(first: str, second: str) -> int:
    return len(os.path.commonprefix([first, second]))


def _end_of(text: str, marker: str) -> int:
    return text.index(marker) + len(marker)


# --- Engineer ---------------------------------------------------------------


def _engineer(monkeypatch, tmp_path: Path, *, task: str, next_action: str | None, cycle: int, compact_team: bool = False) -> str:
    monkeypatch.setattr(engineer_prompts, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer_prompts, "native_shell_summary", lambda: "")
    return build_mission_prompt(
        task=task,
        skill_text="## Skill libraries (on-demand)\nRole: engineer.",
        next_action=next_action,
        original_request="Make the cache hold.",
        role_banner="Vertical policy for the Engineer.",
        project_root=tmp_path,
        compact_team=compact_team,
        operator_context=(
            "## OperatorContext\n"
            f"- directive [mission]: cycle {cycle}\n"
            f"operator_context_revision={cycle}"
        ),
    )


def test_engineer_static_rules_precede_every_mission_specific_line(monkeypatch, tmp_path) -> None:
    first = _engineer(monkeypatch, tmp_path, task="Fix the prefix.", next_action=None, cycle=1)
    second = _engineer(
        monkeypatch, tmp_path,
        task="Fix the suffix instead.",
        next_action="Address the Reviewer's finding.",
        cycle=2,
    )

    static_end = _end_of(first, "NEXT_OWNER=reviewer")
    assert _lcp(first, second) >= static_end
    # The recalled Skills, the request and the task follow the static rules.
    assert first.index("## Skill libraries") > static_end
    assert first.index("## Original operator request") > static_end
    assert first.index("## Current mission task") > static_end
    assert second.index("## Reviewer guidance from prior round") > second.index("## Current mission task")
    assert second.rstrip().endswith("operator_context_revision=2")


def test_engineer_compact_team_prompt_keeps_the_same_shape(monkeypatch, tmp_path) -> None:
    first = _engineer(monkeypatch, tmp_path, task="Fix the prefix.", next_action=None, cycle=1, compact_team=True)
    second = _engineer(monkeypatch, tmp_path, task="Fix the suffix instead.", next_action=None, cycle=2, compact_team=True)

    static_end = _end_of(first, "NEXT_OWNER=reviewer")
    assert _lcp(first, second) >= static_end
    assert first.index("## Skill libraries") > static_end
    assert first.index("Fix the prefix.") > first.index("## Skill libraries")


def test_engineer_round_tail_follows_the_mission_text(monkeypatch, tmp_path) -> None:
    """Per-round blocks land after the whole mission text, before the trailing
    OperatorContext — even when the mission text itself quotes such a block."""
    quoted = (
        "## OperatorContext\nquoted by the mission text\n"
        "operator_context_revision=3\n---\n## Live objective\nShip it."
    )
    prompt = _engineer(monkeypatch, tmp_path, task="Do the work.\n\n" + quoted, next_action=None, cycle=4)

    first = assemble_round_prompt(
        prompt,
        role_context="## Active research context\nnotes after round 0",
        checkpoint_block="## MissionBrief\n- Decisive result: round 1",
        external_work_advisory="## External work status\n- job-1: running",
    )
    second = assemble_round_prompt(
        prompt,
        role_context="## Active research context\nnotes after round 1",
        checkpoint_block="## MissionBrief\n- Decisive result: round 2",
        external_work_advisory="",
    )

    mission_end = _end_of(first, "Ship it.")
    assert _lcp(first, second) >= mission_end
    assert first.index("## Active research context") > mission_end
    assert first.index("## MissionBrief") > first.index("## Active research context")
    assert first.rindex("## OperatorContext") > first.index("## External work status")
    assert first.rstrip().endswith("operator_context_revision=4")


def test_engineer_round_tail_is_appended_when_no_operator_context_trails(monkeypatch, tmp_path) -> None:
    quoted = "## OperatorContext\nquoted only\noperator_context_revision=3\n---\n## Live objective\nShip it."
    prompt = build_mission_prompt(
        task="Do the work.\n\n" + quoted, skill_text="", next_action=None, project_root=tmp_path,
    )

    assembled = assemble_round_prompt(prompt, checkpoint_block="## MissionBrief\n- round 1")

    assert assembled.startswith(prompt)
    assert assembled.rstrip().endswith("- round 1")


# --- Planner ----------------------------------------------------------------


def _planner(tmp_path: Path, cycle: int, *, resume: bool = False) -> str:
    build = build_continuous_resume_prompt if resume else build_continuous_prompt
    return build(
        continuous_objective="Reach the target.",
        journal_tail=f"cycle {cycle} settled work",
        research_plan=f"# Research plan\ncycle {cycle}",
        planning_cycle=cycle,
        runtime_change_summary=f"cycle {cycle} runtime facts",
        project_root=tmp_path,
        state_root=tmp_path,
    )


def test_planner_prompt_keeps_policy_and_brief_ahead_of_cycle_facts(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    first, second = _planner(tmp_path, 1), _planner(tmp_path, 2)

    brief_end = _end_of(first, "## Manager mission brief (authoritative)\nReach the target.")
    assert _lcp(first, second) >= brief_end
    # The runtime hygiene rule is static and now sits in the stable prefix.
    assert first.index("## Runtime\n") < brief_end
    for volatile in ("## Journal of completed work", "## Current reality", "planning cycle #"):
        assert first.index(volatile) > brief_end, volatile


def test_planner_resume_delta_keeps_the_brief_ahead_of_cycle_facts(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    first, second = _planner(tmp_path, 1, resume=True), _planner(tmp_path, 2, resume=True)

    brief_end = _end_of(first, "## Manager mission brief (authoritative)\nReach the target.")
    assert _lcp(first, second) >= brief_end


# --- Reviewer ---------------------------------------------------------------


def test_reviewer_static_is_identical_and_delta_opens_with_the_mission(tmp_path) -> None:
    persist_vertical(tmp_path, "software")
    owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})

    def render(round_index: int) -> tuple[str, str]:
        return render_reviewer_prompt(
            owner,
            objective="Implement the fix.",
            original_objective="Make it hold.",
            operator_messages=[f"round {round_index} steering"],
            planner_review_instruction="Verify the behavior.",
            round_index=round_index,
            session_id=f"s-{round_index}",
            main_summary=f"round {round_index} account",
            main_error=None,
            round_max=4,
            prev_review_summary=f"round {round_index - 1} review" if round_index > 1 else "",
            working_dir=tmp_path,
            vertical_state_root=tmp_path,
            vertical="software",
            preselected_skill_block="## Recalled Skills\nmission skill",
        )

    static_one, delta_one = render(1)
    static_two, delta_two = render(2)

    assert static_one == static_two
    # The per-mission Skill block closes the static text, after the stage policy.
    assert static_one.rstrip().endswith("mission skill")
    assert static_one.index("## Completion") < static_one.index("## Recalled Skills")
    guidance_end = _end_of(delta_one, "Planner guidance:\nVerify the behavior.")
    assert _lcp(delta_one, delta_two) >= guidance_end


# --- Manager ----------------------------------------------------------------


def _stage_decision(status: str, reason: str, *, waiting: bool) -> str:
    return build_stage_decision_prompt(
        current_stage="experiment",
        next_stage="paper",
        later_stages=("paper", "review"),
        earlier_stages=("idea",),
        checklist_md="- [ ] experiment.result — the decisive run exists",
        review=SimpleNamespace(status=status, reason=reason),
        planner_verdict=SimpleNamespace(
            waiting=waiting,
            waiting_reason="waiting on the run",
            waiting_contract=None,
            reason="hold",
        ),
        continuous_objective="Write the paper.",
    )


def test_manager_stage_prompt_keeps_objective_and_checklist_ahead_of_the_evidence() -> None:
    first = _stage_decision("continue", "Round 1: the run is half done.", waiting=False)
    second = _stage_decision("done", "Round 2: the run finished.", waiting=True)

    checklist_end = _end_of(first, "## What the current stage requires\n- [ ] experiment.result — the decisive run exists")
    assert _lcp(first, second) >= checklist_end
    assert second.index("## Planner-wait reconciliation") > checklist_end
    assert "Include `resolves_wait` when the Planner is waiting" in second
    assert "Include `resolves_wait`" not in first
    assert first.index("## Latest completion evidence") > checklist_end


def test_manager_assembly_leads_with_the_vertical_banner() -> None:
    prompt = assemble_manager_prompt(
        "Decide.",
        role_banner="Vertical policy for the Manager.",
        role_skill_block="## Skill libraries (on-demand)\npaths",
        role_context="## Active research context\nnotes",
    )

    assert prompt.startswith(MODEL_INTEGRITY_BOUNDARY)
    assert (
        prompt.index("## Active vertical Manager skill")
        < prompt.index("Decide.")
        < prompt.index("## Active research context")
        < prompt.index("## Skill libraries")
    )


# --- Research vertical: static inventory vs. live facts ---------------------


def test_gpu_inventory_is_static_while_memory_in_use_is_live(monkeypatch) -> None:
    readings = [[("0", "NVIDIA RTX A6000", 45.0, 1.0)]]
    monkeypatch.setattr(prompt_policy, "_query_local_gpus", lambda: readings[0])
    first_static = prompt_policy.local_hardware_block()
    first_live = prompt_policy.local_hardware_usage_block()
    readings[0] = [("0", "NVIDIA RTX A6000", 45.0, 30.0)]
    second_static = prompt_policy.local_hardware_block()
    second_live = prompt_policy.local_hardware_usage_block()

    assert first_static == second_static
    assert "- GPU 0: NVIDIA RTX A6000, 45 GB memory" in first_static
    assert "GB free" not in first_static
    assert "- GPU 0: 44 GB free, 1 GB in use by running jobs" in first_live
    assert "- GPU 0: 15 GB free, 30 GB in use by running jobs" in second_live


def test_research_fragment_is_static_and_the_context_carries_notes_and_usage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(prompt_policy, "_query_local_gpus", lambda: [("0", "NVIDIA RTX A6000", 45.0, 1.0)])
    monkeypatch.setattr(prompt_policy, "local_model_inventory_block", lambda _root=None: "")
    notes = tmp_path / "RESEARCH_NOTES.md"
    notes.write_text("# Research notes\nround one findings\n", encoding="utf-8")
    roles = (("planner", "continuous"), ("engineer", "mission"), ("manager", "stage_decision"))

    fragments = {}
    for role, operation in roles:
        fragment = prompt_policy.render_role_prompt_fragment(
            role=role, operation=operation, stage="experiment", scope="", project_root=tmp_path,
        )
        context = prompt_policy.render_role_prompt_context(
            role=role, operation=operation, stage="experiment", scope="", project_root=tmp_path,
        )
        fragments[role] = fragment
        assert "round one findings" not in fragment, role
        assert "GB free" not in fragment, role
        assert "round one findings" in context, role
        if role != "manager":
            assert "## Compute available on this machine" in fragment, role
            assert "1 GB in use by running jobs" in context, role

    notes.write_text("# Research notes\nround two findings\n", encoding="utf-8")
    for role, operation in roles:
        assert prompt_policy.render_role_prompt_fragment(
            role=role, operation=operation, stage="experiment", scope="", project_root=tmp_path,
        ) == fragments[role], role


def test_engineer_round_carries_the_research_notes_after_the_static_prompt(monkeypatch, tmp_path) -> None:
    from argus_skill.engineer.round_prompt import RoundPromptMixin

    persist_vertical(tmp_path, "research")
    monkeypatch.setattr(prompt_policy, "_query_local_gpus", lambda: [])
    (tmp_path / "RESEARCH_NOTES.md").write_text("# Research notes\nlive findings\n", encoding="utf-8")
    mixin = RoundPromptMixin()
    mixin.engineer_config = SimpleNamespace(vertical_state_root=tmp_path)
    mixin.reviewer_config = SimpleNamespace(active_vertical="research", vertical_state_root=str(tmp_path))
    config = SimpleNamespace(
        compact_continuation_prompts=True,
        background_subagent_advisory=False,
        max_rounds=8,
        context_packet_path="",
        engineer_operation="author_draft",
    )

    prompt = mixin._assemble_round_prompt(
        round_index=1,
        supervised_config=config,
        engineer_prompt_builder=lambda _next_action, _include_static: "FULL_STATIC_TEXT",
        reviewer_next_action=None,
        checkpoint_path=None,
        workdir=tmp_path,
        role_session=SimpleNamespace(policy="fresh", action="fresh", prompt_block=lambda: ""),
        on_event=None,
    )

    assert prompt.startswith("FULL_STATIC_TEXT")
    assert "live findings" in prompt
