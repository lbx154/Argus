"""Argus-skill's own MissionPlanner.

Replaces ``codex_autoloop.planner.Planner``. Upstream's planner has a
700-line prompt that demands a JSON workstream table with concrete
checklist items, follow-up flags, etc. — useful for long-running coding
campaigns but heavy for a general-purpose chat agent.

Ours emits the same upstream ``PlanDecision`` shape (so state_store and
mission_runtime keep working) but the prompt simply asks the planner
model: "given the objective and what's happened so far, is there a
useful follow-up sub-objective worth running?". If yes, it gives one;
if no, it sets ``follow_up_required=False`` and we stop.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend


@dataclass
class MissionPlannerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = True
    full_auto: bool = True
    dangerous_yolo: bool = False


def _import_plan_decision_cls():
    try:
        from codex_autoloop.models import PlanDecision  # type: ignore
    except ImportError as exc:  # pragma: no cover — environmental
        raise ImportError(
            "MissionPlanner requires codex_autoloop.models.PlanDecision to be importable."
        ) from exc
    return PlanDecision


class MissionPlanner:
    """One ``evaluate(...)`` call after a successful round (plan_mode=auto)."""

    def __init__(self, runner: RunnerBackend) -> None:
        self.runner = runner
        self._plan_decision_cls = _import_plan_decision_cls()

    def evaluate(
        self,
        *,
        objective: str,
        round_index: int,
        latest_review_summary: str,
        latest_main_summary: str,
        config: MissionPlannerConfig,
        operator_messages: list[str] | None = None,
        plan_direction: str = "",
    ) -> Any:  # codex_autoloop.models.PlanDecision
        prompt = self._build_prompt(
            objective=objective,
            round_index=round_index,
            latest_review_summary=latest_review_summary,
            latest_main_summary=latest_main_summary,
            operator_messages=operator_messages or [],
            plan_direction=plan_direction,
        )
        result = self.runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                dangerous_yolo=config.dangerous_yolo,
                full_auto=config.full_auto,
                skip_git_repo_check=config.skip_git_repo_check,
                extra_args=list(config.extra_args) if config.extra_args else None,
            ),
            run_label="planner",
        )
        agent_messages = list(result.agent_messages or [])
        if not agent_messages:
            return self._no_follow_up("Planner returned empty output.")
        decision = _find_plan_in_messages(agent_messages, self._plan_decision_cls)
        if decision is not None:
            return decision
        # Couldn't parse — default to "no follow-up needed" so the
        # mission ends cleanly instead of looping on a malformed plan.
        return self._no_follow_up("Planner output was not parseable JSON.")

    def _no_follow_up(self, reason: str) -> Any:
        return self._plan_decision_cls(
            follow_up_required=False,
            next_explore="",
            main_instruction="",
            review_instruction="",
            overview_markdown=f"# Plan\n\n- {reason}\n",
        )

    @staticmethod
    def _build_prompt(
        *,
        objective: str,
        round_index: int,
        latest_review_summary: str,
        latest_main_summary: str,
        operator_messages: list[str],
        plan_direction: str,
    ) -> str:
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        direction_block = (
            f"Operator-supplied plan direction:\n{plan_direction}\n\n"
            if plan_direction
            else ""
        )
        return (
            "You are the planner sub-agent for an argus-skill mission. The\n"
            "main objective has just been judged DONE by the reviewer. Your\n"
            "job is to decide whether a follow-up sub-objective would be\n"
            "useful to run automatically, or whether the mission should\n"
            "stop here.\n\n"
            "Return JSON only. No markdown fences. Required keys:\n"
            "- follow_up_required: boolean\n"
            "- next_explore: short string (\"\" if not required)\n"
            "- main_instruction: short string the main agent will be given\n"
            "  next round (\"\" if not required)\n"
            "- review_instruction: short string the reviewer will use next\n"
            "  round to judge the follow-up (\"\" if not required)\n"
            "- overview_markdown: 1–6 line plan overview to record\n\n"
            "Decision rules:\n"
            "1) Default to follow_up_required=false. The mission has\n"
            "   already met the operator's stated objective; only propose a\n"
            "   follow-up if there is an obvious next step that the\n"
            "   operator would want done while the agent is still loaded.\n"
            "2) If you do propose a follow-up, make it self-contained and\n"
            "   completable in one round. Do not propose multi-stage\n"
            "   campaigns.\n"
            "3) Honour the operator-supplied plan direction below if any\n"
            "   is given.\n\n"
            f"Original objective:\n{objective}\n\n"
            f"Operator messages:\n{operator_text}\n\n"
            f"{direction_block}"
            f"Round just completed: {round_index}\n"
            "Reviewer's final summary:\n"
            f"{latest_review_summary or '(none)'}\n\n"
            "Main agent's last reply:\n"
            f"{latest_main_summary or '(empty)'}\n"
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _find_plan_in_messages(messages: list[str], plan_cls: Any) -> Any:
    for msg in reversed(messages):
        result = parse_plan_text(msg, plan_cls)
        if result is not None:
            return result
    return None


def parse_plan_text(text: str, plan_cls: Any) -> Any:
    if not text or not text.strip():
        return None
    candidate = _strip_fences(text)
    obj: Any = None
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    follow = obj.get("follow_up_required")
    if not isinstance(follow, bool):
        return None
    return plan_cls(
        follow_up_required=follow,
        next_explore=str(obj.get("next_explore") or "").strip(),
        main_instruction=str(obj.get("main_instruction") or "").strip(),
        review_instruction=str(obj.get("review_instruction") or "").strip(),
        overview_markdown=str(obj.get("overview_markdown") or "").strip(),
    )


__all__ = [
    "MissionPlanner",
    "MissionPlannerConfig",
    "parse_plan_text",
]
