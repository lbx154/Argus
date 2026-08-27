"""Engineer prompt operations and structured context requests."""

from __future__ import annotations

from pathlib import Path

from ...core.model_visible_text import sanitize_model_visible_text
from ...core.role_decision import decision_footer_instruction
from ..task_contract import (
    EFFECTIVE_TASK_CONTRACT,
    native_shell_contract,
    native_shell_summary,
)
from .types import RoleName, RolePromptRequest

MISSION = "mission"
OPERATIONS = frozenset({MISSION})
_MANAGER_GROUNDING_HEADER = "\n\n## Manager project grounding (advisory evidence)\n"

_POSIX_LONG_EXPERIMENT_RULE = (
    "For >2m commands, use: "
    "`\"${ARGUS_SKILL_PYTHON:-python3}\" -m "
    "argus_skill.tools.subagent submit --task-id <id> --mode direct "
    "--timeout <seconds> --command '<command>'`; `--mode supervised` is only for "
    "semantic monitoring. Keep its receipt, reply when discussing, and never use "
    "a session-owned background shell; do not poll in the foreground. For accelerators, "
    "declare count, memory, duration, checkpointability and intent; never put "
    "nvidia-smi/GPU polling in the command. `waiting_resource` is healthy."
)
def _performance_diagnostic_section(work_kind: str) -> str:
    if work_kind != "engineering_optimization":
        return ""
    return (
        "## Performance diagnosis\n"
        "An end-to-end threshold miss only shows that this run missed its target. Before "
        "claiming a root cause, dominant/bottleneck stage, or replacement "
        "architecture, inspect the code hot path and live resource/wait state, then "
        "obtain phase timing/profiling or a controlled A/B that explains a material "
        "share of elapsed time. Otherwise say that the cause is still unclear, "
        "continue the diagnosis, and do not promote the hypothesis into a Skill."
    )

_WINDOWS_LONG_EXPERIMENT_RULE = (
    "For >2m commands on Windows PowerShell 5.1, use: "
    "`& '.\\.venv\\Scripts\\python.exe' -m argus_skill.tools.subagent submit "
    "--task-id '<id>' --mode direct --timeout '<seconds>' --command '<command>'`. "
    "`--mode supervised` is only for semantic monitoring. Keep its receipt, reply when "
    "discussing, never use a session-owned background shell, and do not poll in "
    "the foreground. For "
    "accelerators, declare count, memory, duration, checkpointability "
    "and intent; never put nvidia-smi/GPU polling in the command. "
    "`waiting_resource` is healthy."
)

def _long_experiment_rule() -> str:
    shell_rule = (
        _WINDOWS_LONG_EXPERIMENT_RULE
        if native_shell_contract()
        else _POSIX_LONG_EXPERIMENT_RULE
    )
    return shell_rule


def append_live_guidance(prompt: str, guidance: list[str]) -> str:
    if not guidance:
        return prompt
    return (
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
    marker = "\n\n## OperatorContext\n"
    stable_prompt, separator, operator_tail = prompt.partition(marker)
    if separator:
        prompt = stable_prompt
    tail = [
        sanitize_model_visible_text(block)
        for block in (
            checkpoint_block,
            background_advisory,
            external_work_advisory,
        )
        if block
    ]
    if separator:
        tail.append("## OperatorContext\n" + operator_tail)
    if not tail:
        return prompt
    return prompt + "\n\n" + "\n\n".join(tail)


def _deduplicated_original_request(original_request: str, task: str) -> str:
    original = original_request.strip()
    current = task.strip()
    if not original or original == current:
        return ""
    if (
        _MANAGER_GROUNDING_HEADER in original
        and _MANAGER_GROUNDING_HEADER in current
    ):
        original_base, original_grounding = original.split(
            _MANAGER_GROUNDING_HEADER,
            1,
        )
        _current_base, current_grounding = current.split(
            _MANAGER_GROUNDING_HEADER,
            1,
        )
        if original_grounding.strip() == current_grounding.strip():
            original = original_base.strip()
    return "" if original == current else original


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
        + "\nDo not turn task-specific hypotheses, causal attributions, failed "
        "attempts, or replacement recommendations into Skills unless phase "
        "attribution/profiling or a controlled comparison verified the causal rule. "
        "Keep inconclusive findings out of Skills.\n"
        "If there is no durable reusable procedure, make no Skill edit."
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
    project_root: Path | str | None = None,
    project_skill_dir: Path | str | None = None,
    compact_team: bool = False,
    work_kind: str = "",
    operator_context: str = "",
) -> str:
    """Build the complete per-round Engineer mission prompt."""
    shell_contract = native_shell_contract()
    shell_summary = native_shell_summary()
    learning_block = _post_task_learning_section(
        require_post_task_learning=require_post_task_learning,
        project_skill_dir=project_skill_dir,
    )
    if compact_team and include_static:
        sections = [EFFECTIVE_TASK_CONTRACT]
        if shell_summary:
            sections.append(shell_summary)
        if role_banner.strip():
            sections.append(
                "## Active vertical role\n"
                + sanitize_model_visible_text(role_banner.strip())
            )
        if skill_text:
            sections.append(skill_text)
        sections.append(task)
        sections.append(
            "## Engineer service\n"
            "Manager fixed scope and Planner delegated this package. Inspect only what "
            "the mission contract needs and implement it end to end. Run the named "
            "feedback-producing check at the size this verification profile needs; "
            "use changed feedback, never repeat an unchanged check. Do not reopen "
            "campaign planning, start another Argus service, or create unrelated "
            "artifacts. Within an explore/develop mission, follow feedback into the "
            "alternative proposal the decision rule authorizes. If a material blocker "
            "remains, preserve only the state needed for one next round."
        )
        if learning_block:
            sections.append(learning_block)
        sections.append(
            "## Engineer receipt\n"
            "Return the material result and decisive check; Reviewer owns acceptance.\n"
            + decision_footer_instruction(
                "MILESTONE_STATUS=done\n"
                "RESULT=material result and decisive check\n"
                "NEXT_OWNER=reviewer"
            )
        )
        from ...core.operator_context import append_operator_context

        return append_operator_context("\n\n".join(sections), operator_context)

    sections: list[str] = [EFFECTIVE_TASK_CONTRACT]
    if shell_summary:
        sections.append(shell_summary)
    delta_sections: list[str] = []
    if role_banner.strip():
        sections.append(
            "## Active vertical role\n"
            + sanitize_model_visible_text(role_banner.strip())
        )
    if skill_text:
        sections.append(skill_text)
    unique_original_request = _deduplicated_original_request(
        original_request,
        task,
    )
    if unique_original_request:
        sections.append(
            "## Original operator request\n"
            "Higher-priority live operator instructions may update this; "
            "lower-authority guidance may not silently change it.\n\n"
            + unique_original_request
        )
    sections.append("## Current mission task\n" + task)
    diagnostic_block = _performance_diagnostic_section(work_kind)
    if diagnostic_block:
        sections.append(diagnostic_block)
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
            sections.append(sanitize_model_visible_text(knowledge_block))
    if next_action:
        delta_sections.append(
            "## Reviewer guidance from prior round\n"
            "The previous round was judged incomplete. Address the\n"
            "following before declaring done:\n\n"
            + sanitize_model_visible_text(next_action)
        )
    sections.append(
        "## This turn\n"
        "Own this task end to end. Plan your own steps, use tools, and iterate until "
        "the task passes its check or reaches a real blocker. Work in the current "
        "directory; pure reading without an artifact or measurement is not progress. "
        "Write only the code this task needs; do not add hashes, UUIDs, retries, "
        "fallbacks, locks, or abstractions without a concrete requirement. "
        "Unless required, do not write planning/spec/brief documents, initialize Git, "
        "branch/worktree, or commit; Planner owns the campaign plan. Delegate wide "
        "reading, sweeps and long runs to subagents; take back the answer, not the "
        "transcript. Your context is the budget.\n"
        "Never repeat unchanged checks or reads. Ignore `__pycache__`/`.pyc`; "
        "Python tests already import code, so avoid compile-only ceremony.\n"
        "Use primary sources when external behavior matters. If repeated attempts fail, "
        "recheck the underlying assumption instead of making another cosmetic tweak.\n"
        + _long_experiment_rule()
    )
    if learning_block:
        sections.append(learning_block)
    sections.append(
        "## Handoff\n"
        "CHECKPOINT.md is the only role-maintained cross-round handoff file; do not create "
        "handoff or evidence packets. Host invokes Reviewer only when required; do not "
        "spawn a Reviewer subagent. Normally set next_owner=reviewer. Use operator only "
        "for a real operator decision; include one operator_question and at most five "
        "operator_options; that parks the task, so record it and yield. Options use "
        "`id::label::description`, or `id::true::label::description` when a note "
        "is required.\n\n"
        + decision_footer_instruction(
            "MILESTONE_STATUS=done\n"
            "RESULT=what changed and the decisive check\n"
            "NEXT_OWNER=reviewer"
        )
    )
    static_text = "\n\n".join(sections)
    delta_text = "\n\n".join(delta_sections)
    if include_static:
        from ...core.operator_context import append_operator_context

        prompt = static_text + ("\n\n" + delta_text if delta_text else "")
        return append_operator_context(prompt, operator_context)
    compact = (
        "## Continuation turn\n"
        "Read CHECKPOINT.md, then execute the Reviewer next action. Do not repeat an "
        "unchanged failure; use the most informative decisive diagnostic. The original task "
        "still applies.\n"
        + _long_experiment_rule()
        + "\n\n"
        "## Handoff\n"
        "Use next_owner=operator only for an operator-owned choice; its question "
        "parks the task. Include operator_question and operator_options in that "
        "decision.\n"
        + decision_footer_instruction(
            "MILESTONE_STATUS=done\n"
            "RESULT=short result and decisive check\n"
            "NEXT_OWNER=reviewer"
        )
    )
    if diagnostic_block:
        compact = diagnostic_block + "\n\n" + compact
    if shell_contract:
        compact = shell_contract + "\n\n" + compact
    if learning_block:
        compact += "\n\n" + learning_block
    from ...core.operator_context import append_operator_context

    prompt = compact + ("\n\n" + delta_text if delta_text else "")
    return append_operator_context(prompt, operator_context)


def mission_request(
    project_root: Path | str,
    *,
    vertical: str | None = None,
    altitude_root: Path | str | None = None,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.ENGINEER,
        operation=MISSION,
        project_root=project_root,
        # Where the work is. The vertical fragment describes the workspace, and
        # project_root here is the vertical state root, which contains no paper.
        altitude_root=altitude_root,
        vertical=vertical,
    )


__all__ = [
    "MISSION",
    "OPERATIONS",
    "append_live_guidance",
    "assemble_round_prompt",
    "build_mission_prompt",
    "mission_request",
]
