"""Scientist / Distiller skill authoring.

The Scientist is a role call used when the matcher finds no reusable engineer
skill. It writes an immediately active project-layer playbook; later real task
trajectories and Reviewer-authored ops evolve or retire that version.
"""
from __future__ import annotations

import logging
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
    ) -> None:
        self.runner = runner
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.last_result: Any = None

    def distill(self, task: str) -> str:
        """Return skill markdown, or ``""`` on failure/no useful skill."""
        return self._run(_build_scientist_prompt(task))

    def distill_alternative(self, task: str, reviewer_evidence: str) -> str:
        """Author a different playbook after the current one proves ineffective."""
        return self._run(_build_alternative_prompt(task, reviewer_evidence))

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
                ),
                run_label="scientist.skill_distill",
                resume_thread_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("scientist skill distill failed: %s", exc)
            return ""
        self.last_result = result
        text = (
            getattr(result, "last_agent_message", "")
            or (getattr(result, "agent_messages", None) or [""])[-1]
            or ""
        )
        text = str(text).strip()
        if not text or text.upper().startswith("NONE"):
            return ""
        return text


def _build_scientist_prompt(task: str) -> str:
    return (
        "You are the Scientist / Distiller role for argus-skill. The skill "
        "matcher found no reusable engineer playbook for the task below. Write "
        "ONE reusable skill that can help the Engineer execute this "
        "task now and a FAMILY of similar future tasks.\n\n"
        "Rules:\n"
        "- Do not solve the task directly; write the playbook the Engineer should "
        "use.\n"
        "- Make it general: use placeholders such as <path>, <command>, <metric>, "
        "and do not hardcode one-off mission IDs or local absolute paths unless "
        "the family genuinely requires them.\n"
        "- Include enough operational detail for an Engineer to act without a "
        "second explanation.\n"
        "- Before writing, use live web search to check current primary sources; "
        "do not invent facts or citations.\n"
        "- If there is truly no reusable pattern, output exactly NONE.\n\n"
        "Required markdown shape:\n"
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
        f"## Task\n{task.strip()}\n"
    )


def _build_alternative_prompt(task: str, reviewer_evidence: str) -> str:
    return (
        "You are the Scientist / Distiller role for argus-skill. A matched "
        "playbook has repeatedly failed independent review. Use live web search "
        "and author ONE genuinely different reusable playbook for the task family.\n\n"
        "Do not merely rephrase the existing approach. Treat the reviewer evidence "
        "as failed mechanisms to avoid. Do not solve the task directly or claim "
        "success; provide an operational strategy the Engineer can try next. If no "
        "defensible alternative exists, output exactly NONE.\n\n"
        "Required markdown sections: Description, Category, When to use, When NOT "
        "to use, How to solve, Pitfalls.\n\n"
        f"## Task\n{task.strip()}\n\n"
        f"## Reviewer evidence from failed rounds\n{reviewer_evidence.strip()}\n"
    )


__all__ = ["SkillScientist"]
