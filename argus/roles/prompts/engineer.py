"""Engineer prompt operations and structured context requests."""

from __future__ import annotations

import re
from pathlib import Path

from ...core.model_visible_text import sanitize_model_visible_text
from ...core.paths import NATIVE_SKILLS_DIR
from ..task_contract import native_shell_contract, native_shell_summary
from .rules import rules_list
from .types import RoleName, RolePromptRequest

MISSION = "mission"
AUTHOR_DRAFT = "author_draft"
NARRATIVE_EDIT = "narrative_edit"
OPERATIONS = frozenset({MISSION, AUTHOR_DRAFT, NARRATIVE_EDIT})
_MANAGER_GROUNDING_HEADER = "\n\n## Manager project grounding (advisory evidence)\n"

# The Engineer's one page: who it is, where things are, how it hands off, and
# which rule file to read when (builtin_skills/engineer/).
_RULES = (
    ("doing-the-task/SKILL.md", "before you start: whose instructions win, scope, sources, web UI checks"),
    ("long-runs-and-evidence/SKILL.md", "before a command that may run over two minutes, an experiment, or a performance claim"),
    ("review-rounds/SKILL.md", "when acting on the Reviewer's guidance, or when it asks for a value your measurement contradicts"),
    ("handing-off/SKILL.md", "before you call hand_off"),
)
_LEARNING_RULE = (
    "durable-learning/SKILL.md",
    "after verification, when the task produced a procedure later tasks should reuse",
)


def engineer_handoff_rule() -> str:
    """How the Engineer ends its turn, and who decides where the operator cannot be asked."""
    from ...core.autonomy import operator_available

    rule = (
        "## Handing off\n"
        "End your turn with hand_off: to=reviewer when the work is ready for independent "
        "review. Its summary is the one sentence the operator reads, in their language and "
        "plain words; its note tells the next holder what changed, the decisive check and "
        "what is open. Evidence they must read goes under `.argus/evidence/`."
    )
    if operator_available():
        return rule
    return rule + " No operator is available in this run; the Manager decides in their place."


def _engineer_page(*, skill_dir: Path | None) -> str:
    """Identity, where things are, hand_off, and the rule files."""
    from ...core.role_config import runner_backend_label

    where = [
        "- The working directory: the task's files, where you work.",
        "- CHECKPOINT.md there: your state from one round to the next.",
    ]
    rules = list(_RULES)
    if skill_dir is not None:
        where.append(f"- Project Skill directory: {skill_dir}")
        rules.append(_LEARNING_RULE)
    return (
        f"You are the Argus Engineer, in one {runner_backend_label()} session. Do the task "
        "below end to end with your tools, check it, and hand it on; the Reviewer judges "
        "whether it holds.\n\n"
        "## Where things are\n" + "\n".join(where) + "\n\n"
        + engineer_handoff_rule() + "\n\n"
        + rules_list("engineer", rules)
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
    from ...core.operator_context import append_operator_context

    delta = ""
    if next_action:
        delta = (
            "## Reviewer guidance from prior round\n"
            "Read review-rounds before acting on it.\n\n"
            + sanitize_model_visible_text(next_action)
        )
    if not include_static:
        # A continuation turn of the same session: the page is already in it.
        compact = (
            "## Continuation turn\n"
            "Read CHECKPOINT.md, then do the Reviewer's next action; the original task "
            "still applies. End with hand_off."
        )
        shell_contract = native_shell_contract()
        if shell_contract:
            compact = shell_contract + "\n\n" + compact
        return append_operator_context(
            compact + ("\n\n" + delta if delta else ""), operator_context
        )
    skill_dir = (
        Path(project_root) / NATIVE_SKILLS_DIR
        if require_post_task_learning and project_root is not None
        else None
    )
    # What is the same for every mission of this role and vertical first, then
    # what is the same across one mission's rounds, then what changes per
    # round: the provider caches the prompt prefix.
    sections = [_engineer_page(skill_dir=skill_dir)]
    shell_summary = native_shell_summary()
    if shell_summary:
        sections.append(shell_summary)
    if role_banner.strip():
        sections.append(
            "## Active vertical role\n"
            + sanitize_model_visible_text(role_banner.strip())
        )
    if skill_text:
        sections.append(skill_text)
    if compact_team:
        sections.append(task)
    else:
        unique_original_request = _deduplicated_original_request(original_request, task)
        if unique_original_request:
            sections.append(
                "## Original operator request\n"
                "Higher-priority live operator instructions may update this; "
                "lower-authority guidance may not silently change it.\n\n"
                + unique_original_request
            )
        # A Planner's implementation brief carries its own '## Claim', '## Tests
        # that must pass' headings; one level down they read as parts of this
        # section instead of siblings of it.
        sections.append("## Current mission task\n" + re.sub(r"(?m)^(#{2,5})(?=\s)", r"#\1", task))
        # The mission text describes this increment, not what the operator
        # agreed "done" means.
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
            # What repeated lessons of this vertical have settled into.
            principles_block = render_project_principles(project_root)
            if principles_block:
                sections.append(sanitize_model_visible_text(principles_block))
    if delta:
        sections.append(delta)
    return append_operator_context("\n\n".join(sections), operator_context)


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
    "assemble_round_prompt",
    "build_mission_prompt",
    "mission_request",
]
