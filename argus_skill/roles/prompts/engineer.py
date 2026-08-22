"""Engineer prompt operations and structured context requests."""

from __future__ import annotations

import re
from pathlib import Path

from ...core.model_visible_text import sanitize_model_visible_text
from ...core.role_decision import decision_event_instruction
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
    "For commands over two minutes, use Argus's durable runner: "
    "`\"${ARGUS_SKILL_PYTHON:-python3}\" -m "
    "argus_skill.tools.subagent submit --task-id <id> --mode direct "
    "--timeout <seconds> --command '<command>'`. Use `--mode supervised` only for "
    "semantic monitoring. Never `task(mode=\"background\")` or a session-owned "
    "background shell. Keep the `state=submitted`, `task_id`, `run_id` and "
    "`check_with` receipt; on `state=discussing` answer with `reply_with` "
    "and do not poll in the foreground."
)
_PERFORMANCE_DIAGNOSTIC_TASK = re.compile(
    r"\b(?:throughput|latency|performance|bottleneck|profil(?:e|ing|er)?|"
    r"resource|cpu|gpu|scal(?:e|ing|ability)|benchmark)\b|"
    r"吞吐|性能|瓶颈|延迟|剖析",
    re.IGNORECASE,
)
_AUDIT_FIDELITY_TASK = re.compile(
    r"\b(?:audit|ledger|command[ _-]?log|process[ _-]?trace|append[ _-]?only|"
    r"provenance)\b|审计|账本|问题记录|命令日志|过程记录|只追加|来源归因",
    re.IGNORECASE,
)


def _audit_fidelity_section(task: str) -> str:
    if not _AUDIT_FIDELITY_TASK.search(task):
        return ""
    return (
        "## Audit fidelity\n"
        "An objective or inherited summary is a requirement, not observed evidence. "
        "Attribute a fact to it only when the cited text actually says that fact; "
        "otherwise label the claim unverified until a command or immutable artifact "
        "establishes it. If the operator freezes mutation or names a ledger append-only, "
        "stop installs and repairs immediately: never replace that file, even after "
        "copying or archiving it; add a correction only through a verified append path. "
        "Capture each result-bearing shell command byte-faithfully in a sidecar before "
        "summarizing it. Do not embed Markdown backticks in an unquoted heredoc: use a "
        "single-quoted delimiter or a literal file API, and judge inner stderr/status "
        "rather than trusting an outer shell exit 0."
    )


def _performance_diagnostic_section(task: str) -> str:
    if not _PERFORMANCE_DIAGNOSTIC_TASK.search(task):
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
    "For commands over two minutes on native Windows, use Windows PowerShell 5.1 syntax "
    "and Argus's durable runner: "
    "`& '.\\.venv\\Scripts\\python.exe' -m argus_skill.tools.subagent submit "
    "--task-id '<id>' --mode direct --timeout '<seconds>' --command '<command>'`. "
    "Use `--mode supervised` only for semantic monitoring. Do not use "
    "`task(mode=\"background\")` or a session-owned background shell. Keep the "
    "`state=submitted`, `task_id`, `run_id`, and `check_with` receipt. On "
    "`state=discussing`, answer with `reply_with`; do not poll in the foreground."
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
        if skill_text:
            sections.append(skill_text)
        sections.append(task)
        sections.append(
            "## Engineer service\n"
            "Manager fixed scope and Planner delegated this package. Inspect only what "
            "the mission contract needs, implement it end to end, and run the named or "
            "smallest decisive check once. Do not reopen planning, start another Argus "
            "service, broaden research, or create extra artifacts. If a material blocker "
            "remains, preserve only the state needed for one next round."
        )
        if learning_block:
            sections.append(learning_block)
        sections.append(
            "## Engineer receipt\n"
            "Return the material result and decisive check; Reviewer owns acceptance.\n"
            + decision_event_instruction(
                "engineer",
                '{"status":"done","result":"material result and decisive check",'
                '"next_owner":"reviewer"}',
            )
        )
        return sanitize_model_visible_text("\n\n".join(sections))

    sections: list[str] = [EFFECTIVE_TASK_CONTRACT]
    if shell_summary:
        sections.append(shell_summary)
    delta_sections: list[str] = []
    if role_banner.strip():
        sections.append("## Active vertical role\n" + role_banner.strip())
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
    diagnostic_block = _performance_diagnostic_section(task)
    if diagnostic_block:
        sections.append(diagnostic_block)
    audit_fidelity_block = _audit_fidelity_section(task)
    if audit_fidelity_block:
        sections.append(audit_fidelity_block)
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
        "Own this task end to end. Plan your own steps, use tools, and iterate until "
        "the task passes its check or reaches a real blocker. Work in the current "
        "directory; pure reading without an artifact or measurement is not progress. "
        "Write only the code this task needs; do not add hashes, UUIDs, retries, "
        "fallbacks, locks, or abstractions without a concrete requirement. "
        "Unless required, do not write planning/spec/brief documents, initialize Git, "
        "branch/worktree, commit, or spawn subagents. Use subagents for operator-requested "
        "parallelism or useful independent work.\n"
        "Never repeat unchanged checks/reads; batch tools and cap results at 200 "
        "lines. At 18 tool calls, synthesize or checkpoint/yield; never exceed 24.\n"
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
        "operator_options; that parks the task, so record it and yield.\n\n"
        + decision_event_instruction(
            "engineer",
            '{"status":"done","result":"what changed and the decisive check",'
            '"next_owner":"reviewer"}',
        )
    )
    static_text = "\n\n".join(sections)
    delta_text = "\n\n".join(delta_sections)
    if include_static:
        return sanitize_model_visible_text(
            static_text + ("\n\n" + delta_text if delta_text else "")
        )
    compact = (
        "## Continuation turn\n"
        "Read CHECKPOINT.md, then execute the Reviewer next action. Do not repeat an "
        "unchanged failure; use the cheapest decisive diagnostic. The original task "
        "still applies.\n"
        + _long_experiment_rule()
        + "\n\n"
        "## Handoff\n"
        "Use next_owner=operator only for an operator-owned choice; its question "
        "parks the task. Include operator_question and operator_options in that "
        "decision.\n"
        + decision_event_instruction(
            "engineer",
            '{"status":"done","result":"short result and decisive check",'
            '"next_owner":"reviewer"}',
        )
    )
    if diagnostic_block:
        compact = diagnostic_block + "\n\n" + compact
    if audit_fidelity_block:
        compact = audit_fidelity_block + "\n\n" + compact
    if shell_contract:
        compact = shell_contract + "\n\n" + compact
    if learning_block:
        compact += "\n\n" + learning_block
    return sanitize_model_visible_text(
        compact + ("\n\n" + delta_text if delta_text else "")
    )


def mission_request(
    project_root: Path | str,
    *,
    vertical: str | None = None,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.ENGINEER,
        operation=MISSION,
        project_root=project_root,
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
