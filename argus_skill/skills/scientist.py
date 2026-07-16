"""Scientist / Distiller skill authoring.

The Scientist is a role call used when the matcher finds no reusable engineer
skill. It writes an immediately active project-layer playbook; later real task
trajectories and Reviewer-authored ops evolve or retire that version.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec

log = logging.getLogger(__name__)


class SkillScientist:
    """Author one reusable skill for a task."""

    def __init__(
        self,
        runner: Any,
        *,
        model: str = "",
        reasoning_effort: str = "high",
        role_banner: str = "",
        max_budget_usd: float | None = None,
    ) -> None:
        self.runner = runner
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.role_banner = str(role_banner or "").strip()
        self.max_budget_usd = max_budget_usd
        self.last_result: Any = None

    def distill(self, task: str) -> str:
        """Return skill markdown, or ``""`` on failure/no useful skill."""
        return self._run(_build_scientist_prompt(task, self.role_banner))

    def distill_alternative(
        self,
        task: str,
        reviewer_evidence: str,
        *,
        current_skill: str = "",
        method_history: str = "",
    ) -> str:
        """Author a different playbook after the current one proves ineffective."""
        return self._run(
            _build_alternative_prompt(
                task,
                reviewer_evidence,
                role_banner=self.role_banner,
                current_skill=current_skill,
                method_history=method_history,
            )
        )

    def _run(self, prompt: str) -> str:
        if self.runner is None:
            return ""
        self.last_result = None
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                options=RunnerOptions(
                    model=self.model or None,
                    reasoning_effort=self.reasoning_effort,
                    skip_git_repo_check=True,
                    full_auto=True,
                    live_search=True,
                    max_budget_usd=self.max_budget_usd,
                ),
                run_label="scientist.skill_distill",
                resume_thread_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("scientist skill distill failed: %s", exc)
            return ""
        self.last_result = result
        if (
            int(getattr(result, "exit_code", 0) or 0) != 0
            or getattr(result, "fatal_error", None)
        ):
            return ""
        text = (
            getattr(result, "last_agent_message", "")
            or (getattr(result, "agent_messages", None) or [""])[-1]
            or ""
        )
        text = str(text).strip()
        if not text or text.upper().startswith("NONE"):
            return ""
        return text


def _build_scientist_prompt(task: str, role_banner: str = "") -> str:
    task_context = str(task or "").strip()
    prompt = (
        "You are Argus's Skill Distiller. The matcher found no reusable Engineer "
        "playbook for the task below. Your single goal is to distill exactly one "
        "high-quality reusable skill that helps an Engineer execute this task and "
        "future tasks from the same family. Read the complete task context before "
        "deciding what capability and method the skill must teach.\n\n"
        "Hard boundaries:\n"
        "- Do not solve the current task or write/modify project files.\n"
        "- Do not invoke skills or plugins. Do not launch subagents. Do not create "
        "todos, plans, tests, SQL queries, shell commands, or meta-workflows.\n"
        "- Do not assume a prior failed method or invent reviewer evidence.\n"
        "- Use live web search as much as needed to verify current primary sources "
        "and make the reusable method accurate.\n"
        "- Generalize with placeholders such as <path>, <source>, and <criterion>; "
        "omit mission IDs, local absolute paths, and one-off project details.\n"
        "- Return the final Markdown only. If no reusable method is defensible, "
        "output exactly NONE.\n\n"
        "Output shape:\n"
        "# <skill title>\n"
        "## Description\n"
        "<what capability this skill provides>\n"
        "## Category\n"
        "<category slug>\n"
        "## When to use\n"
        "- ...\n"
        "## When NOT to use\n"
        "- ...\n"
        "## How to solve\n"
        "1. ...\n"
        "## Pitfalls\n"
        "- ...\n\n"
        f"## Complete task context (read-only; do not execute)\n{task_context}\n"
    )
    return _prepend_role_banner(prompt, role_banner)


def _build_alternative_prompt(
    task: str,
    reviewer_evidence: str,
    *,
    role_banner: str = "",
    current_skill: str = "",
    method_history: str = "",
) -> str:
    task_context = str(task or "").strip()
    prompt = (
        "You are the Scientist / Distiller role for argus-skill. A matched "
        "playbook has repeatedly failed independent review. Use live web search "
        "and author ONE genuinely different reusable playbook for the task family.\n\n"
        "Do not invoke skills/plugins, "
        "launch subagents, create todos/plans/tests/SQL, run shell commands, or "
        "modify project files. Use web search as much as needed.\n\n"
        "Do not merely rephrase the existing approach. Treat the reviewer evidence "
        "as failed mechanisms to avoid. Changing only constants, bounds, prompts, "
        "search depth, or other parameters is NOT a new mechanism. Do not solve the "
        "task directly or claim success; provide an operational strategy the Engineer "
        "can try next. If no defensible alternative exists, output exactly NONE.\n\n"
        "Required markdown sections: Description, Category, When to use, When NOT "
        "to use, Mechanism change, How to solve, Pitfalls. In `Mechanism change`, "
        "use exactly these fields:\n"
        "Previous mechanism: <failed mechanism>\n"
        "Replacement mechanism: <different mechanism>\n"
        "Structural difference: <why this is not a parameter change>\n\n"
        f"## Complete task context\n{task_context}\n\n"
        f"## Current playbook\n{current_skill.strip() or '(none)'}\n\n"
        f"## Prior method ledger\n{method_history.strip() or '(none)'}\n\n"
        f"## Reviewer evidence from failed rounds\n{reviewer_evidence.strip()}\n"
    )
    return _prepend_role_banner(prompt, role_banner)


def _prepend_role_banner(prompt: str, role_banner: str) -> str:
    banner = str(role_banner or "").strip()
    if not banner:
        return prompt
    return f"## Active vertical role\n{banner}\n\n{prompt}"


def parse_mechanism_change(skill_markdown: str) -> dict[str, str] | None:
    """Parse the Scientist's explicit structural-mechanism declaration."""
    match = re.search(
        r"^## Mechanism change\s*$([\s\S]*?)(?=^## |\Z)",
        str(skill_markdown or ""),
        flags=re.MULTILINE,
    )
    if match is None:
        return None
    block = match.group(1)
    fields: dict[str, str] = {}
    for label, key in (
        ("Previous mechanism", "previous"),
        ("Replacement mechanism", "replacement"),
        ("Structural difference", "difference"),
    ):
        field = re.search(
            rf"^{re.escape(label)}:\s*(.+)$",
            block,
            flags=re.MULTILINE,
        )
        if field is None:
            return None
        fields[key] = field.group(1).strip()[:1000]
    previous = " ".join(fields["previous"].casefold().split())
    replacement = " ".join(fields["replacement"].casefold().split())
    if (
        not previous
        or not replacement
        or previous == replacement
        or len(fields["difference"]) < 20
    ):
        return None
    return fields


__all__ = ["SkillScientist", "parse_mechanism_change"]
