"""Scientist / Distiller skill authoring.

The Scientist is a role call used when the matcher finds no reusable engineer
skill. It writes a provisional playbook for the current task; the Engineer uses
that playbook immediately, and the Reviewer later proves or rejects it through
the existing provisional skill lifecycle.
"""
from __future__ import annotations

import logging
from typing import Any

from ..core.models import RunnerOptions

log = logging.getLogger(__name__)


class SkillScientist:
    """Author one reusable skill candidate for a task."""

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
        if self.runner is None:
            return ""
        self.last_result = None
        prompt = _build_scientist_prompt(task)
        try:
            result = self.runner.run_exec(
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
        "ONE provisional, reusable skill that can help the Engineer execute this "
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


__all__ = ["SkillScientist"]
