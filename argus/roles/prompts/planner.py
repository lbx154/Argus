"""Planner prompt operations and structured context requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...core.model_visible_text import sanitize_model_visible_text
from ..task_contract import native_shell_summary
from .rules import rules_list
from .types import ChecklistMode, RoleName, RolePromptRequest

CONTINUOUS = "continuous"
PLAN_PREVIEW = "plan_preview"
PARALLEL_DRAFT = "parallel_draft"

OPERATIONS = frozenset(
    {
        CONTINUOUS,
        PLAN_PREVIEW,
        PARALLEL_DRAFT,
    }
)


_RESEARCH_PLAN_HEADER = "## Research plan (RESEARCH_PLAN.md)\n"


def _planner_page(
    *, workspace: Path, state: Path, wikis: list[Path], rules: list[tuple[str, str]]
) -> str:
    """The Planner's one page: who it is, where things are, hand_off, and the rule files."""
    from ...core.paths import reviewed_facts_digest_path
    from ...core.role_config import runner_backend_label

    where = [
        f"- Mission workspace, the only place you plan from: {workspace}",
        f"- Argus state: {state} (RESEARCH_PLAN.md, backlog.jsonl, events.jsonl)",
        f"- Reviewed facts: {reviewed_facts_digest_path().resolve()}",
        *(f"- Shared project Wiki: {wiki.resolve()}" for wiki in wikis),
    ]
    return (
        f"You are the Argus Planner, in one {runner_backend_label()} session. Read the "
        "project and its state, never edit them, and choose the most valuable next work "
        "for the Engineer, who implements and tests.\n\n"
        "## Where things are\n" + "\n".join(where) + "\n\n"
        "## Handing off\n"
        "End your turn with hand_off: the tasks to start, the pending work to retire, any "
        "stage to move to, and who acts next; to=done only when the operator's complete "
        "goal is met. Its summary is the decision and next step in the operator's language "
        "and plain words.\n\n"
        + rules_list("planner", rules)
    )


def _join_prompt_blocks(*blocks: str) -> str:
    """Join only applicable prompt modules with one stable separator."""
    rendered = [block.strip() for block in blocks if block and block.strip()]
    return "\n\n".join(rendered) + "\n"


def _operator_context_block(
    state_root: Path | str | None, runtime_change_summary: str
) -> str:
    """The Planner's OperatorContext, unless the caller already rendered it.

    The Supervisor puts the projection it will acknowledge, live turn included,
    into the runtime digest. Reading the store again here sent every continuous
    Planner prompt two OperatorContext blocks, and the second could be newer
    than the revision the Supervisor acknowledges.
    """
    from ...core.operator_context import build_operator_context_block

    if state_root is None or "## OperatorContext" in runtime_change_summary:
        return ""
    operator_context, _revision = build_operator_context_block(
        "planner", state_root, consume_once=False
    )
    return operator_context


def continuous_request(
    project_root: Path | str,
    *,
    stage: str | None = None,
    operation: str = CONTINUOUS,
    include_search_altitude: bool = True,
    include_role_context: bool = False,
    altitude_root: Path | str | None = None,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        altitude_root=altitude_root,
        operation=operation,
        project_root=project_root,
        stage=stage,
        checklist_mode=ChecklistMode.STAGE,
        include_search_altitude=include_search_altitude,
        include_role_context=include_role_context,
    )


def preview_request(project_root: Path | str) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        operation=PLAN_PREVIEW,
        project_root=project_root,
    )


def build_continuous_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    research_plan: str = "",
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    open_ended: bool = False,
    memory_maintenance_enabled: bool = True,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
    trailing_policy: str = "",
) -> str:
    """Build the continuous Planner prompt from the unified role catalog."""
    from ...core.project import resolve_project_root
    from ...core.project_contract import contract_briefing, load_contract_for_cwd
    from ...core.research_contract import resolve_research_target_level
    from ...skills.vertical_select import resolve_workflow_mode
    from ...wiki.context import render_operator_memory_block, render_project_principles
    from .registry import resolve_role_prompt

    _workspace = resolve_project_root(project_root)
    _proot = resolve_project_root(state_root) if state_root is not None else _workspace
    prompt_context = resolve_role_prompt(
        continuous_request(_proot, altitude_root=_workspace, include_role_context=True)
    )
    stage = prompt_context.stage
    stage_order = list(prompt_context.stage_order)
    direct = resolve_workflow_mode(_proot) == "direct"

    # What this cycle is: the stage-and-target rule file says what each part means.
    situation = ["direct work" if direct else "staged work"]
    stage_lines = [
        f"- current: `{stage}`"
        + (" (`workflow_mode=direct`: the stage names the kind of work)" if direct else ""),
        f"- sequence: {', '.join(stage_order) or '(none)'}",
    ]
    research_target_level = resolve_research_target_level(_proot)
    if research_target_level is not None or prompt_context.verification_stage_profiles:
        from ...core.verification_policy import resolve_policy
        from ...skills.stage_machine import current_stage

        try:
            policy_stage = current_stage(_proot)
        except Exception:  # noqa: BLE001 - stage is advisory here
            policy_stage = ""
        profile = resolve_policy(
            stage=policy_stage,
            stage_profiles=prompt_context.verification_stage_profiles,
        ).profile
        stage_lines.append(f"- this round's verification profile: `{profile}`")
        if research_target_level is not None:
            stage_lines.append(f"- research target: `{research_target_level}`")
            situation.append("a research target")
        else:
            situation.append("verification staging")
    if stage_order and stage == stage_order[-1] and (
        prompt_context.completion_gate == "certified" or research_target_level is not None
    ):
        stage_lines.append(
            '- final stage: the certification task sets `scope: "final_submission"`'
        )
        situation.append("the final certification task")
    if open_ended:
        stage_lines.append("- open-ended: continue until the operator stops the work")
        situation.append("open-ended work")
    research_vertical = prompt_context.vertical == "research"
    rules = [
        ("dependency-aware-task-decomposition.md", "before choosing, splitting or retiring tasks"),
        ("reality-and-sources/SKILL.md", "before relying on a fact, a source, a measurement or the Wiki"),
        ("stages-and-targets/SKILL.md", "before planning around the stage; this cycle covers: " + ", ".join(situation)),
    ]
    if not research_vertical:
        rules.append(("research-plan/SKILL.md", "when RESEARCH_PLAN.md below is missing or the direction changes"))
    if os.environ.get("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", "").strip():
        rules.append(("external-target/SKILL.md", "before proposing work: the target is an external scorer"))
    autors_root = _workspace / ".autors"
    wikis = [
        wiki
        for wiki in (sorted(autors_root.glob("*/wiki")) if autors_root.exists() else [])
        if (wiki / "INDEX.md").is_file() and (wiki / "pages").is_dir()
    ]
    # The Planner gets the same library paths as other roles and searches them
    # itself; no Skill content is copied into this prompt.
    skill_block = mission.libraries().block if mission is not None else ""
    principles_block = sanitize_model_visible_text(render_project_principles(_proot))
    # Plans are made for a person; what Argus knows about the operator frames them.
    operator_block = sanitize_model_visible_text(render_operator_memory_block())
    # The hard criteria the Manager recorded from what the operator said.
    goal_contract_block = contract_briefing(
        load_contract_for_cwd(_workspace),
        authoritative_objective=continuous_objective,
    )
    research_plan_context = "" if research_vertical else (
        _RESEARCH_PLAN_HEADER
        + sanitize_model_visible_text(
            research_plan.strip()
            or "(no plan yet — create RESEARCH_PLAN.md in this planning cycle)"
        )
    )
    # Order: what is the same for every cycle of this vertical and stage, then
    # what is the same for the whole campaign (the brief), then what changes
    # between cycles. The provider caches the prompt prefix.
    return _join_prompt_blocks(
        _planner_page(workspace=_workspace, state=_proot, wikis=wikis, rules=rules),
        native_shell_summary(),
        prompt_context.role_banner,
        "" if direct else prompt_context.stage_checklist,
        "## Current workflow stage\n" + "\n".join(stage_lines),
        skill_block,
        operator_block,
        principles_block,
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        goal_contract_block,
        sanitize_model_visible_text(prompt_context.role_context),
        sanitize_model_visible_text(prompt_context.search_altitude),
        "## Journal of completed work (most recent last)\n"
        + sanitize_model_visible_text(
            journal_tail.strip()
            or "(no completed work yet — this is the first cycle)"
        ),
        research_plan_context,
        "## Current reality (authoritative over the journal above)\n"
        + sanitize_model_visible_text(
            runtime_change_summary.strip() or "(no additional runtime context)"
        ),
        f"This is planning cycle #{planning_cycle + 1}.",
        trailing_policy,
        _operator_context_block(state_root, runtime_change_summary),
    )


def build_continuous_resume_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    research_plan: str = "",
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
    trailing_policy: str = "",
    journal_is_delta: bool = False,
    research_plan_unchanged: bool = False,
) -> str:
    """Render only the changing Planner delta for a resumable role session.

    The prior same-role turn already contains the immutable Planner contract,
    vertical policy, and tool boundary.  Repeating that large preamble on every
    cycle defeats provider prompt caching; this delta still carries the current
    stage/checklist, durable objective, journal, and fresh runtime facts.

    ``journal_is_delta`` marks ``journal_tail`` as only the entries that
    settled after this session's previous planning turn — the earlier turns of
    the same thread already hold the older ones, so repeating them buys
    nothing. ``research_plan_unchanged`` likewise stands in for a plan document
    the session has already read in full.
    """
    from ...core.project import resolve_project_root
    from .registry import resolve_role_prompt

    workspace = resolve_project_root(project_root)
    state = resolve_project_root(state_root) if state_root is not None else workspace
    prompt_context = resolve_role_prompt(
        continuous_request(state, altitude_root=workspace, include_role_context=True)
    )
    if prompt_context.vertical == "research":
        research_plan_context = ""
    elif research_plan_unchanged:
        research_plan_context = (
            "## Research plan\n"
            "RESEARCH_PLAN.md is unchanged since your previous planning turn; "
            "the copy already in this session is still current."
        )
    else:
        research_plan_context = _RESEARCH_PLAN_HEADER + sanitize_model_visible_text(
            research_plan.strip()
            or "(no plan yet — create RESEARCH_PLAN.md in this planning cycle)"
        )
    if journal_is_delta:
        journal_block = (
            "## Newly settled work since your previous planning turn "
            "(most recent last)\n"
            + sanitize_model_visible_text(
                journal_tail.strip()
                or (
                    "(nothing new has settled since your previous planning "
                    "turn; the journal you already hold is still current)"
                )
            )
        )
    else:
        journal_block = (
            "## Journal of completed work (most recent last)\n"
            + sanitize_model_visible_text(
                journal_tail.strip()
                or "(no completed work yet — this is the first cycle)"
            )
        )
    skill_block = ""
    if mission is not None:
        try:
            libraries = mission.libraries()
            skill_block = str(getattr(libraries, "block", "") or "")
        except Exception:  # noqa: BLE001 - a resume delta must remain available
            skill_block = ""
    operator_context = _operator_context_block(state_root, runtime_change_summary)
    return _join_prompt_blocks(
        "## Continued Planner cycle\n"
        "You are resuming your own Planner session; its first page still holds. "
        "Current state below supersedes stale session facts. A rule file or Skill "
        "you already read in this session is still in your context; open it again "
        "only if its text is gone.",
        str(prompt_context.role_banner or ""),
        "## Current workflow stage\n"
        f"- current: `{prompt_context.stage}`\n"
        f"- sequence: {', '.join(prompt_context.stage_order) or '(none)'}\n"
        + str(prompt_context.stage_checklist or ""),
        skill_block,
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        # Live vertical facts change between cycles, which is exactly what a
        # resume delta is for — the header above already promises that current
        # state supersedes stale session facts. Omitting them meant a resumed
        # Planner never saw its vertical's altitude at all: the search floor and
        # frozen count for a metric campaign, or the accepted papers pulled to
        # disk for a paper campaign. Each vertical still renders only its own.
        sanitize_model_visible_text(prompt_context.role_context),
        sanitize_model_visible_text(prompt_context.search_altitude or ""),
        journal_block,
        research_plan_context,
        "## Current reality (authoritative over the journal above)\n"
        + sanitize_model_visible_text(
            runtime_change_summary.strip() or "(no additional runtime context)"
        ),
        f"This is planning cycle #{planning_cycle + 1}.",
        "Inspect only what is needed to choose the next concrete task or a real "
        "obstacle, then end with hand_off.",
        trailing_policy,
        operator_context,
    )


__all__ = [
    "CONTINUOUS",
    "OPERATIONS",
    "PARALLEL_DRAFT",
    "PLAN_PREVIEW",
    "build_continuous_prompt",
    "build_continuous_resume_prompt",
    "continuous_request",
    "preview_request",
]
