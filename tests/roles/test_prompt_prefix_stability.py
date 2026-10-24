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

from argus.roles.prompts.manager import (
    build_stage_decision_prompt,
)
from argus.roles.prompts.planner import (
    build_continuous_prompt,
    build_continuous_resume_prompt,
)
from argus.roles.prompts.reviewer import render_reviewer_prompt
from argus.skills.vertical_select import persist_vertical
from argus.verticals.research import prompt_policy


def _lcp(first: str, second: str) -> int:
    return len(os.path.commonprefix([first, second]))


def _end_of(text: str, marker: str) -> int:
    return text.index(marker) + len(marker)


# --- Engineer ---------------------------------------------------------------


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


def _stage_decision(status: str, reason: str) -> str:
    return build_stage_decision_prompt(
        current_stage="experiment",
        next_stage="paper",
        later_stages=("paper", "review"),
        earlier_stages=("idea",),
        checklist_md="- [ ] experiment.result — the decisive run exists",
        review=SimpleNamespace(status=status, reason=reason),
        planner_verdict=SimpleNamespace(reason="hold"),
        continuous_objective="Write the paper.",
    )


def test_manager_stage_prompt_keeps_objective_and_checklist_ahead_of_the_evidence() -> None:
    first = _stage_decision("continue", "Round 1: the run is half done.")
    second = _stage_decision("done", "Round 2: the run finished.")

    checklist_end = _end_of(first, "## What the current stage requires\n- [ ] experiment.result — the decisive run exists")
    assert _lcp(first, second) >= checklist_end
    assert first.index("## Latest completion evidence") > checklist_end


# --- Research vertical: static policy vs. live notes ------------------------


def test_research_fragment_is_static_and_the_context_carries_notes(tmp_path) -> None:
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
        assert "round one findings" in context, role

    notes.write_text("# Research notes\nround two findings\n", encoding="utf-8")
    for role, operation in roles:
        assert prompt_policy.render_role_prompt_fragment(
            role=role, operation=operation, stage="experiment", scope="", project_root=tmp_path,
        ) == fragments[role], role


def test_engineer_round_carries_the_research_notes_after_the_static_prompt(tmp_path) -> None:
    from argus.engineer.round_prompt import RoundPromptMixin

    persist_vertical(tmp_path, "research")
    (tmp_path / "RESEARCH_NOTES.md").write_text("# Research notes\nlive findings\n", encoding="utf-8")
    mixin = RoundPromptMixin()
    mixin.engineer_config = SimpleNamespace(vertical_state_root=tmp_path)
    mixin.reviewer_config = SimpleNamespace(active_vertical="research", vertical_state_root=str(tmp_path))
    config = SimpleNamespace(
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
