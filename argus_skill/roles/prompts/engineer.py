"""Engineer prompt operations and structured context requests."""

from __future__ import annotations

from pathlib import Path

from ...core.model_visible_text import sanitize_model_visible_text
from ..task_contract import EFFECTIVE_TASK_CONTRACT, native_shell_contract
from .types import RoleName, RolePromptRequest

MISSION = "mission"
OPERATIONS = frozenset({MISSION})

_LONG_EXPERIMENT_RULE = (
    "For commands expected to run over two minutes, launch a supervised "
    "subagent, record its run id, and yield or do independent work. Never hold "
    "the provider turn open with foreground shell execution or polling."
)


def append_live_guidance(prompt: str, guidance: list[str]) -> str:
    if not guidance:
        return sanitize_model_visible_text(prompt)
    return sanitize_model_visible_text(
        prompt
        + "\n\n## LIVE MANAGER / OPERATOR DIRECTIVES — HIGHEST PRIORITY\n"
        + "These directives may stop, narrow, or correct the current mission. "
        + "They do not silently broaden a structured bounded task or cross its "
        + "pipeline stage. If a directive materially replaces the current "
        + "bounded objective, preserve state, update CHECKPOINT.md, and request "
        + "Reviewer/Planner replanning instead of executing the new scope here.\n"
        + "\n".join(f"- {item}" for item in guidance)
    )


def assemble_round_prompt(
    prompt: str,
    *,
    checkpoint_block: str = "",
    background_advisory: str = "",
    external_work_advisory: str = "",
) -> str:
    """Append all dynamic Engineer round fragments in one stable order."""
    tail = [
        block
        for block in (
            checkpoint_block,
            background_advisory,
            external_work_advisory,
        )
        if block
    ]
    if not tail:
        return sanitize_model_visible_text(prompt)
    return sanitize_model_visible_text(prompt + "\n\n" + "\n\n".join(tail))


def _post_task_learning_section(
    *,
    require_post_task_learning: bool,
    project_skill_dir: Path | str | None,
) -> str:
    """Render the Engineer's own durable-learning contract.

    The Engineer ends the task with the full execution context, making it the
    right place to retain a reusable procedure. Roles edit the project Skill
    layer directly, so the contract names the destination explicitly.
    """
    if not require_post_task_learning or project_skill_dir is None:
        return ""
    from ...skills.role_memory import role_skill_edit_rules

    rules = role_skill_edit_rules("engineer", project_skill_dir)
    return (
        "## Durable learning\n"
        "You have file and shell tools. After verification, if this task "
        "produced durable procedures that would change how future tasks are "
        "done, create or update the applicable Engineer Skills directly in the "
        "project skill directory before you hand off.\n"
        + rules
        + "\nIf there is no durable reusable procedure, make no Skill edit."
    )


def build_mission_prompt(
    *,
    task: str,
    skill_text: str,
    next_action: str | None,
    original_request: str = "",
    include_static: bool = True,
    role_banner: str = "",
    require_post_task_learning: bool = False,
    file_read_budget: int = 12,
    test_run_budget: int = 3,
    project_root: Path | str | None = None,
    project_skill_dir: Path | str | None = None,
) -> str:
    """Build the complete per-round Engineer mission prompt."""
    sections: list[str] = [EFFECTIVE_TASK_CONTRACT]
    shell_contract = native_shell_contract()
    if shell_contract:
        sections.append(shell_contract)
    delta_sections: list[str] = []
    if role_banner.strip():
        sections.append("## Active vertical role\n" + role_banner.strip())
    if skill_text:
        sections.append("## Skill library paths\n" + skill_text)
    if original_request.strip():
        sections.append(
            "## Original operator request\n"
            "Higher-priority live operator instructions may update this; "
            "lower-authority guidance may not silently change it.\n\n" + original_request.strip()
        )
    sections.append("## Current mission task\n" + task)
    # The Engineer is the role that can most easily satisfy a task while
    # missing the requirement the task exists to serve — the mission text
    # describes this increment, not what the operator agreed "done" means.
    from ...core.project_contract import contract_briefing, load_contract_for_cwd

    contract_block = contract_briefing(
        load_contract_for_cwd(),
        authoritative_objective=original_request,
    )
    if contract_block:
        sections.append(contract_block)
    if project_root is not None:
        from ...wiki.context import render_knowledge_wiki_block

        knowledge_block = render_knowledge_wiki_block(
            project_root,
            role="Engineer",
        )
        if knowledge_block:
            sections.append(knowledge_block)
    if next_action:
        delta_sections.append(
            "## Reviewer guidance from prior round\n"
            "The previous round was judged incomplete. Address the\n"
            "following before declaring done:\n\n" + next_action
        )
    sections.append(
        "## This turn\n"
        "Land one coherent, verifiable increment; update "
        "CHECKPOINT.md, then yield. Pure reading without an artifact or "
        "measurement is not progress.\n"
        "Work in the current directory. Unless required, do not write "
        "planning/spec/brief documents, initialize Git, branch/worktree, commit, "
        "spawn subagents, or invoke meta-workflows.\n"
        f"Budget: inspect about {max(1, int(file_read_budget))} relevant files "
        "before editing and avoid rereads; run at most "
        f"{max(1, int(test_run_budget))} focused verification commands plus the "
        "decisive verifier. Exceed only after a concrete failure or code change. "
        "Use `.autors/*/wiki` only for durable declarative knowledge.\n" + _LONG_EXPERIMENT_RULE
    )
    learning_block = _post_task_learning_section(
        require_post_task_learning=require_post_task_learning,
        project_skill_dir=project_skill_dir,
    )
    if learning_block:
        sections.append(learning_block)
    sections.append(
        "## Handoff\n"
        "If you changed code, build it before you call the work finished: "
        "compile or type-check the packages you touched, not just the tests you "
        "happened to run. The tests in front of you are not necessarily the "
        "tests you are judged on, so a rename, a signature, or an import you "
        "left unreconciled can still break the build for whoever compiles it "
        "next. It costs seconds and is the cheapest failure to catch before the "
        "independent Reviewer examines the round.\n"
        "An empty `git diff` is evidence only for tracked paths: check with "
        "`git ls-files --error-unmatch -- <path>` first. For untracked or "
        "outside-repository artifacts, verify their direct content instead.\n"
        "End with a short, natural account of what changed and the decisive "
        "check or observation. Do not recite a checklist or build an evidence "
        "packet; include only details the next researcher needs. A fresh Reviewer "
        "handles acceptance; do not spawn a Reviewer subagent.\n"
    )
    static_text = "\n\n".join(sections)
    delta_text = "\n\n".join(delta_sections)
    if include_static:
        return sanitize_model_visible_text(
            static_text + ("\n\n" + delta_text if delta_text else "")
        )
    compact = (
        "## Continuation turn\n"
        "Read the shared CHECKPOINT.md first. Execute its current Next Action "
        "and the Reviewer guidance below. Do not repeat an unchanged failing "
        "command; reduce it to the cheapest decisive diagnostic. The original "
        "task, active vertical, and repository instructions remain binding.\n"
        + _LONG_EXPERIMENT_RULE
        + "\n\n"
        "## Handoff\n"
        "End with a concise natural summary and decisive check. If you changed "
        "code, build the packages you touched before calling it done."
    )
    if shell_contract:
        compact = shell_contract + "\n\n" + compact
    if learning_block:
        compact += "\n\n" + learning_block
    return sanitize_model_visible_text(
        compact + ("\n\n" + delta_text if delta_text else "")
    )


def mission_request(project_root: Path | str) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.ENGINEER,
        operation=MISSION,
        project_root=project_root,
    )


__all__ = [
    "MISSION",
    "OPERATIONS",
    "append_live_guidance",
    "assemble_round_prompt",
    "build_mission_prompt",
    "mission_request",
]
