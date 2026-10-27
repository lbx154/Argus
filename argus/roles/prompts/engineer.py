"""Engineer prompt operations and structured context requests."""

from __future__ import annotations

import re
from pathlib import Path

from ...core.model_visible_text import (
    MASKED_DISPLAY_ENGINEER_RULE,
    sanitize_model_visible_text,
)
from ...core.paths import NATIVE_SKILLS_DIR
from ..task_contract import (
    EFFECTIVE_TASK_CONTRACT,
    EXPERIMENT_EVIDENCE_CONTRACT,
    native_shell_contract,
    native_shell_summary,
)
from .types import RoleName, RolePromptRequest
from .voice import RESEARCHER_VOICE

MISSION = "mission"
AUTHOR_DRAFT = "author_draft"
NARRATIVE_EDIT = "narrative_edit"
OPERATIONS = frozenset({MISSION, AUTHOR_DRAFT, NARRATIVE_EDIT})
_MANAGER_GROUNDING_HEADER = "\n\n## Manager project grounding (advisory evidence)\n"

# A long job runs inside the Engineer's own model call: Argus does not wait for
# it. Every check is a model call, so the Engineer waits on it once where the
# backend allows: Argus raises Codex's single-wait limit and Copilot's
# background-task wait to their maximums (_sandbox_commands, _prompt_delivery),
# and Copilot resumes a turn when its async shell exits.
_WAIT_BY_BACKEND = (
    ": on Codex, one `write_stdin` with empty input and a `yield_time_ms` longer "
    "than the job; on Copilot, the shell tool in `async` mode, then end your turn, "
    "and Copilot resumes you when it exits; elsewhere, the longest blocking calls "
    "your shell allows. Never poll in short loops."
)
# The bubblewrap sandbox runs on until everything started in it has exited
# (core.sandbox), so the turn's end does not cut a detached job off.
_JOBS_OUTLIVE_THE_CLI = (
    " A detached job keeps running after you hand off, so stop servers you no "
    "longer need."
)
_POSIX_LONG_EXPERIMENT_RULE = (
    "For commands over two minutes, start them detached so they outlive your "
    "shell, keeping the exit code: `setsid nohup sh -c '<command>; echo $? > "
    "<run>/exit_code' > <run>/log 2>&1 < /dev/null &`. Do the work that does not "
    "need the result, then wait for it on `until [ -e <run>/exit_code ]; do "
    "sleep 30; done`" + _WAIT_BY_BACKEND + _JOBS_OUTLIVE_THE_CLI
)
# The Engineer ends its turn with the one action every role ends with
# (core.handoff); what each holder means is in the action's own description.
_HANDOFF_RULE = (
    "## Handing off\n"
    "End your turn with hand_off. Its summary is the one sentence the operator reads: "
    "what was done and found, in their language and plain words. Its note is for the "
    "next holder: changes with output paths, the decisive check, what is open, and why "
    "it goes to them. The next holder sees only the working directory, so a log or "
    "output they must read is a file under `.argus/evidence/` there, never in your "
    "CLI's own session folders."
)


def engineer_handoff_rule() -> str:
    """How the Engineer ends its turn, and who decides where the operator cannot be asked."""
    from ...core.autonomy import operator_available

    return _HANDOFF_RULE if operator_available() else _HANDOFF_RULE + (
        " No operator is available in this run; the Manager decides in their place. "
        "Settle what a requirement means yourself, on the most defensible reading, and "
        "write it in assumptions. Never assume facts or results, or fake credentials or "
        "services: a missing input, or an action only the operator could approve "
        "(credentials, spending, or an irreversible or external step), goes to=manager."
    )


_PERFORMANCE_DIAGNOSTIC_RULE = (
    "Performance root-cause/bottleneck/replacement claims need hot-path/live-resource "
    "evidence plus timing/profiling or controlled A/B. Correctness smoke tests can "
    "share hardware, but latency evidence needs an uncontended device or an "
    "explicitly matched concurrent load. Check actual device identity and other "
    "processes; free memory or a zero-utilization snapshot does not prove isolation. "
    "Independent process repetitions can run sequentially on a released device."
)
_EXPERIMENT_PREREQUISITE_RULE = (
    "Block dependents on failed readiness; keep each attempt's outputs separate."
)
_SCOPE_RULE = (
    "Fix reported symptoms within design rules; intended behavior needs task-text support. "
    "Add no work the task does not ask for (checks of your own change are part of the task), "
    "and never get around a limit the environment sets "
    "(an offline switch, a disabled package index): when it blocks what you need, say so "
    "and hand off instead of forcing another way in."
)

_WINDOWS_LONG_EXPERIMENT_RULE = (
    "For commands over two minutes on native Windows, use Windows PowerShell 5.1 "
    "syntax: start them detached with `Start-Process -PassThru -WindowStyle Hidden "
    "-FilePath <exe> -ArgumentList <args> -RedirectStandardOutput <run>\\log.txt "
    "-RedirectStandardError <run>\\err.txt`, "
    "keep the process id, do the work that does not need the result, then wait "
    "for it on `Wait-Process -Id <pid>`" + _WAIT_BY_BACKEND
)

def _long_experiment_rule() -> str:
    shell_rule = (
        _WINDOWS_LONG_EXPERIMENT_RULE
        if native_shell_contract()
        else _POSIX_LONG_EXPERIMENT_RULE
    )
    return " ".join((
        shell_rule, _EXPERIMENT_PREREQUISITE_RULE, EXPERIMENT_EVIDENCE_CONTRACT,
    ))


def append_live_guidance(prompt: str, guidance: list[str]) -> str:
    if not guidance:
        return prompt
    return (
        prompt
        + "\n\n## LIVE MANAGER / OPERATOR DIRECTIVES — HIGHEST PRIORITY\n"
        + "These directives may stop, narrow, or correct the current mission. "
        + "They do not silently broaden the task as assigned or cross its "
        + "stage. If a directive materially replaces the current "
        + "objective, preserve state, update CHECKPOINT.md, and request "
        + "Reviewer/Planner replanning instead of executing the new scope here.\n"
        + "\n".join(f"- {item}" for item in guidance)
    )


_OPERATOR_CONTEXT_MARKER = "\n\n## OperatorContext\n"
# ``build_operator_context_block`` closes every block with its revision line,
# which is how the trailing block is told apart from one the mission text
# itself quotes.
_OPERATOR_CONTEXT_END = re.compile(r"(?m)^operator_context_revision=\d+\s*\Z")


def assemble_round_prompt(
    prompt: str,
    *,
    role_context: str = "",
    checkpoint_block: str = "",
    background_advisory: str = "",
) -> str:
    """Append all dynamic Engineer round fragments in one stable order.

    Everything that changes between rounds goes after the prompt the builder
    produced, ahead only of the trailing OperatorContext block, so the static
    text stays a byte-identical prefix. The mission text can itself quote an
    OperatorContext block, so the split point is the LAST marker, and only
    when what follows it is a complete block; otherwise nothing is split.
    """
    stable_prompt, separator, operator_tail = prompt.rpartition(
        _OPERATOR_CONTEXT_MARKER
    )
    if separator and _OPERATOR_CONTEXT_END.search(operator_tail):
        prompt = stable_prompt
    else:
        separator, operator_tail = "", ""
    tail = [
        sanitize_model_visible_text(block)
        for block in (role_context, checkpoint_block, background_advisory)
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
    project_root: Path | str | None,
) -> str:
    """Render the Engineer's own durable-learning contract.

    The Engineer ends the task with the full execution context, making it the
    right place to retain a reusable procedure. It may write only inside its
    workspace, so its Skills are the workspace's Agent Skills, which the host
    reads for this project, recalls for later ones and offers for promotion.
    """
    if not require_post_task_learning or project_root is None:
        return ""
    from ...skills.role_memory import role_skill_edit_rules

    rules = role_skill_edit_rules("engineer", Path(project_root) / NATIVE_SKILLS_DIR)
    return (
        "## Durable learning\n"
        "You have file and shell tools. After verification, if this task "
        "produced durable procedures that would change how future tasks are "
        "done, create or update the applicable Engineer Skills directly in the "
        "project skill directory before you finish this task.\n"
        + rules
        + "\nWrite each Skill as `<name>/SKILL.md`. When a step is better run than "
        "read, keep the script you ran beside it and say in SKILL.md how to run it. "
        "A Skill that belongs to another vertical names it in its front matter: "
        "`vertical: <name>`."
        "\nDo not turn task-specific hypotheses, causal attributions, failed "
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
    compact_team: bool = False,
    operator_context: str = "",
) -> str:
    """Build the complete per-round Engineer mission prompt."""
    shell_contract = native_shell_contract()
    shell_summary = native_shell_summary()
    learning_block = _post_task_learning_section(
        require_post_task_learning=require_post_task_learning,
        project_root=project_root,
    )
    delta_sections: list[str] = []
    if next_action:
        delta_sections.append(
            "## Reviewer guidance from prior round\n"
            "Build on the verified progress and address the following before declaring done. "
            "Final paper review can request high-impact improvements even after the minimum "
            "acceptance bar is met. Implement those changes and validate them, then return "
            "for independent review. Scientific repairs and new experiments for a final "
            "paper happen directly in the current Review stage, without rolling back "
            "or waiting for an earlier-stage mission. For each actionable suggestion, record the actual change "
            "and decisive evidence in the existing CHECKPOINT.md. If a suggested hypothesis "
            "fails, retain the result and explain the evidence-backed alternative; the "
            "Reviewer decides whether that resolves the item. "
            "Never write values that your own labeled measurement contradicts just to "
            "satisfy a review: record the labeled evidence and its command in "
            "CHECKPOINT.md and hand back to the Reviewer asking it to resolve the "
            "discrepancy. " + MASKED_DISPLAY_ENGINEER_RULE + "\n\n"
            + sanitize_model_visible_text(next_action)
        )
    # Section order in both shapes below: what is the same for every mission
    # of this role and vertical first (contract, banner, the rules and the
    # decision footer), then what is the same across the rounds of one
    # mission (recalled Skills, the request, the task), then what changes
    # per round. The provider caches the prompt prefix, so the tail is where
    # change belongs; the Reviewer prompt has the same shape.
    if compact_team and include_static:
        sections = [EFFECTIVE_TASK_CONTRACT]
        if shell_summary:
            sections.append(shell_summary)
        if role_banner.strip():
            sections.append(
                "## Active vertical role\n"
                + sanitize_model_visible_text(role_banner.strip())
            )
        sections.append(_PERFORMANCE_DIAGNOSTIC_RULE)
        sections.append(_long_experiment_rule())
        sections.append(
            "## Engineer service\n"
            "Implement this task end to end; run its profile's named feedback check. "
            "In explore/develop, follow feedback into alternatives authorized by the "
            "decision rule. " + _SCOPE_RULE
        )
        if learning_block:
            sections.append(learning_block)
        sections.append(
            RESEARCHER_VOICE + "\n\n"
            "## Engineer summary\n"
            "Return the material result and decisive check; Reviewer judges whether the work holds.\n\n"
            + engineer_handoff_rule()
        )
        if skill_text:
            sections.append(skill_text)
        sections.append(task)
        sections.extend(delta_sections)
        from ...core.operator_context import append_operator_context

        return append_operator_context("\n\n".join(sections), operator_context)

    sections: list[str] = [EFFECTIVE_TASK_CONTRACT]
    if shell_summary:
        sections.append(shell_summary)
    if role_banner.strip():
        sections.append(
            "## Active vertical role\n"
            + sanitize_model_visible_text(role_banner.strip())
        )
    sections.append(
        "## This turn\n"
        "Own this task end to end: plan and use tools until its check passes or a real "
        "obstacle remains.\n"
        "Web UI: test real Chromium and mobile.\n"
        "Use primary sources when external behavior matters. "
        + _SCOPE_RULE + "\n"
        + _PERFORMANCE_DIAGNOSTIC_RULE
        + "\n"
        + _long_experiment_rule()
    )
    if learning_block:
        sections.append(learning_block)
    sections.append(
        "## Carrying context between rounds\n"
        "CHECKPOINT.md carries your state from one round to the next.\n\n"
        + RESEARCHER_VOICE + "\n\n"
        + engineer_handoff_rule()
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
        from ...wiki.context import (
            render_knowledge_wiki_block,
            render_project_principles,
            shared_knowledge_roots,
        )

        knowledge_block = render_knowledge_wiki_block(
            project_root,
            role="Engineer",
            shared_roots=shared_knowledge_roots(project_root),
        )
        if knowledge_block:
            sections.append(sanitize_model_visible_text(knowledge_block))
        # What repeated lessons of this vertical have settled into: short,
        # bounded, and present only once the vertical has compiled principles.
        principles_block = render_project_principles(project_root)
        if principles_block:
            sections.append(sanitize_model_visible_text(principles_block))
    static_text = "\n\n".join(sections)
    delta_text = "\n\n".join(delta_sections)
    if include_static:
        from ...core.operator_context import append_operator_context

        prompt = static_text + ("\n\n" + delta_text if delta_text else "")
        return append_operator_context(prompt, operator_context)
    compact = (
        "## Continuation turn\n"
        "Read CHECKPOINT.md, then execute the Reviewer next action. If your own labeled "
        "measurement contradicts a value the review asks for, do not write that value: "
        "record the evidence in CHECKPOINT.md and ask the Reviewer to resolve the "
        "discrepancy. The original task still applies.\n"
        + _PERFORMANCE_DIAGNOSTIC_RULE
        + "\n"
        + _long_experiment_rule()
        + "\n\n"
        + RESEARCHER_VOICE + "\n\n"
        + engineer_handoff_rule()
    )
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
    stage: str | None = None,
    operation: str = MISSION,
    include_role_context: bool = False,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.ENGINEER,
        operation=operation,
        project_root=project_root,
        # Where the work is. The vertical fragment describes the workspace, and
        # project_root here is the vertical state root, which contains no paper.
        altitude_root=altitude_root,
        vertical=vertical,
        stage=stage,
        include_role_context=include_role_context,
    )


__all__ = [
    "AUTHOR_DRAFT",
    "MISSION",
    "NARRATIVE_EDIT",
    "OPERATIONS",
    "append_live_guidance",
    "assemble_round_prompt",
    "build_mission_prompt",
    "mission_request",
]
