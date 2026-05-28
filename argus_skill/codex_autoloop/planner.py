from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .checks import summarize_checks
from .codex_runner import CodexRunner, RunnerOptions
from .models import CheckResult, PlanDecision, PlanSnapshot, PlanWorkstream, RoundSummary, ReviewDecision
from .planner_modes import PLANNER_MODE_AUTO, PLANNER_MODE_RECORD


@dataclass
class PlannerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    mode: str = PLANNER_MODE_AUTO


class Planner:
    def __init__(self, runner: CodexRunner) -> None:
        self.runner = runner
        self.schema_path = str(Path(__file__).with_name("planner_schema.json"))

    def update(
        self,
        *,
        objective: str,
        operator_messages: list[str],
        round_index: int,
        session_id: str | None,
        rounds: list[RoundSummary],
        latest_review: ReviewDecision | None,
        latest_checks: list[CheckResult],
        trigger: str,
        terminal: bool,
        stop_reason: str | None,
        config: PlannerConfig,
    ) -> PlanSnapshot:
        prompt = self._build_prompt(
            objective=objective,
            operator_messages=operator_messages,
            round_index=round_index,
            session_id=session_id,
            rounds=rounds,
            latest_review=latest_review,
            latest_checks=latest_checks,
            trigger=trigger,
            terminal=terminal,
            stop_reason=stop_reason,
            mode=config.mode,
        )
        result = self.runner.run_exec(
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                dangerous_yolo=config.dangerous_yolo,
                full_auto=config.full_auto,
                skip_git_repo_check=config.skip_git_repo_check,
                extra_args=config.extra_args,
                output_schema_path=self.schema_path,
            ),
            run_label="planner",
        )
        parsed = _find_plan_in_messages(result.agent_messages)
        if parsed is None:
            return self._fallback_snapshot(
                objective=objective,
                latest_review=latest_review,
                latest_checks=latest_checks,
                trigger=trigger,
                terminal=terminal,
                error=result.last_agent_message or f"Planner returned empty output. exit={result.exit_code}",
            )

        if config.mode == PLANNER_MODE_RECORD:
            parsed.should_propose_follow_up = False
            parsed.suggested_next_objective = ""
        parsed.plan_id = uuid4().hex[:12]
        parsed.generated_at = datetime.now(timezone.utc).isoformat()
        parsed.trigger = trigger
        parsed.terminal = terminal
        parsed.report_markdown = format_plan_markdown(
            objective=objective,
            snapshot=parsed,
            review=latest_review,
            checks=latest_checks,
            stop_reason=stop_reason,
        )
        return parsed

    def evaluate(
        self,
        *,
        objective: str,
        plan_messages: list[str],
        round_index: int,
        session_id: str | None,
        latest_review_completion_summary: str,
        latest_plan_overview: str,
        main_summary: str = "",
        config: PlannerConfig,
    ) -> PlanDecision:
        plan, _ = self.evaluate_with_raw_output(
            objective=objective,
            plan_messages=plan_messages,
            round_index=round_index,
            session_id=session_id,
            latest_review_completion_summary=latest_review_completion_summary,
            latest_plan_overview=latest_plan_overview,
            main_summary=main_summary,
            config=config,
        )
        return plan

    def evaluate_with_raw_output(
        self,
        *,
        objective: str,
        plan_messages: list[str],
        round_index: int,
        session_id: str | None,
        latest_review_completion_summary: str,
        latest_plan_overview: str,
        main_summary: str = "",
        config: PlannerConfig,
    ) -> tuple[PlanDecision, str]:
        """Evaluate and return both PlanDecision and raw JSON output.

        Returns:
            Tuple of (PlanDecision, raw_json_output)
        """
        prompt = self._build_evaluate_prompt(
            objective=objective,
            plan_messages=plan_messages,
            round_index=round_index,
            session_id=session_id,
            latest_review_completion_summary=latest_review_completion_summary,
            latest_plan_overview=latest_plan_overview,
            main_summary=main_summary,
            mode=config.mode,
        )
        result = self.runner.run_exec(
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                dangerous_yolo=config.dangerous_yolo,
                full_auto=config.full_auto,
                skip_git_repo_check=config.skip_git_repo_check,
                extra_args=config.extra_args,
                output_schema_path=self.schema_path,
            ),
            run_label="planner",
        )
        raw_output = result.last_agent_message or ""
        parsed = _find_plan_in_messages(result.agent_messages)
        if parsed is None:
            parsed = self._fallback_snapshot(
                objective=objective,
                latest_review=None,
                latest_checks=[],
                trigger="loop-engine",
                terminal=False,
                error=raw_output or f"Planner returned empty output. exit={result.exit_code}",
            )
        if config.mode == PLANNER_MODE_RECORD:
            parsed.should_propose_follow_up = False
            parsed.suggested_next_objective = ""
        parsed.plan_id = uuid4().hex[:12]
        parsed.generated_at = datetime.now(timezone.utc).isoformat()
        parsed.trigger = "loop-engine"
        parsed.terminal = False
        parsed.report_markdown = format_plan_markdown(
            objective=objective,
            snapshot=parsed,
            review=None,
            checks=[],
            stop_reason=None,
        )
        return (
            self._snapshot_to_decision(
                snapshot=parsed,
                latest_review_completion_summary=latest_review_completion_summary,
            ),
            raw_output,
        )

    def _build_evaluate_prompt(
        self,
        *,
        objective: str,
        plan_messages: list[str],
        round_index: int,
        session_id: str | None,
        latest_review_completion_summary: str,
        latest_plan_overview: str,
        main_summary: str = "",
        mode: str,
    ) -> str:
        plan_messages_text = "\n".join(f"- {line}" for line in plan_messages) if plan_messages else "- none"
        overview_text = latest_plan_overview.strip() if latest_plan_overview.strip() else "none"
        review_summary = latest_review_completion_summary.strip() if latest_review_completion_summary.strip() else "none"
        main_summary_text = main_summary.strip()[:1500] if main_summary.strip() else "none"
        if mode == PLANNER_MODE_RECORD:
            mode_guidance = (
                "Planner mode: record-only.\n"
                "Set should_propose_follow_up=false and leave suggested_next_objective empty.\n\n"
            )
        else:
            mode_guidance = (
                "Planner mode: auto.\n"
                "You may propose one concrete follow-up objective when appropriate.\n\n"
            )
        return (
            "You are the planning manager for an autoloop round.\n"
            "Return valid JSON matching the provided schema.\n"
            "Focus on concrete workstreams, next steps, and risks.\n\n"
            "**Length constraints (strictly enforce):**\n"
            "- Keep `summary` under 500 characters\n"
            "- Keep `evidence` (in workstreams) under 300 characters\n"
            "- Keep `next_step` (in workstreams) under 200 characters\n"
            "- Keep `suggested_next_objective` under 300 characters\n"
            "- Use concise phrases, not full sentences\n"
            "- Avoid code blocks and lengthy explanations\n\n"
            f"{mode_guidance}"
            f"Round index: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n\n"
            "Objective:\n"
            f"{objective}\n\n"
            "Plan-channel operator messages:\n"
            f"{plan_messages_text}\n\n"
            "Main agent last message (what was actually done this round):\n"
            f"{main_summary_text}\n\n"
            "Latest reviewer completion summary:\n"
            f"{review_summary}\n\n"
            "Latest plan overview markdown:\n"
            f"{overview_text}\n"
        )

    @staticmethod
    def _snapshot_to_decision(
        *,
        snapshot: PlanSnapshot,
        latest_review_completion_summary: str,
    ) -> PlanDecision:
        follow_up_required = bool(snapshot.should_propose_follow_up and snapshot.suggested_next_objective.strip())
        next_explore = (
            _first_non_empty(snapshot.exploration_items)
            or _first_non_empty(snapshot.remaining_items)
            or _first_non_empty(snapshot.next_steps)
            or snapshot.summary
            or "Continue execution with current objective."
        )
        main_instruction = (
            snapshot.suggested_next_objective.strip()
            if follow_up_required
            else (
                _first_non_empty(snapshot.next_steps)
                or _first_non_empty(snapshot.remaining_items)
                or snapshot.summary
                or "Continue execution with concrete repository actions."
            )
        )
        review_instruction = (
            _first_non_empty(snapshot.risks)
            or latest_review_completion_summary.strip()
            or "Validate acceptance checks and unresolved risks."
        )
        return PlanDecision(
            follow_up_required=follow_up_required,
            next_explore=next_explore.strip(),
            main_instruction=main_instruction.strip(),
            review_instruction=review_instruction.strip(),
            overview_markdown=snapshot.report_markdown.strip() + "\n",
        )

    def _build_prompt(
        self,
        *,
        objective: str,
        operator_messages: list[str],
        round_index: int,
        session_id: str | None,
        rounds: list[RoundSummary],
        latest_review: ReviewDecision | None,
        latest_checks: list[CheckResult],
        trigger: str,
        terminal: bool,
        stop_reason: str | None,
        mode: str,
    ) -> str:
        operator_text = "\n".join(f"- {line}" for line in operator_messages) if operator_messages else "- none"
        review_text = "none"
        if latest_review is not None:
            review_text = (
                f"status={latest_review.status}\n"
                f"confidence={latest_review.confidence}\n"
                f"reason={latest_review.reason}\n"
                f"next_action={latest_review.next_action}"
            )
        round_lines = []
        for item in rounds[-5:]:
            round_lines.append(
                f"Round {item.round_index}: "
                f"main_failed={item.main_turn_failed} "
                f"review={item.review.status} "
                f"checks_passed={all(check.passed for check in item.checks)}\n"
                f"Main summary: {item.main_last_message.strip()[:600]}\n"
                f"Reviewer reason: {item.review.reason.strip()[:400]}\n"
            )
        rounds_text = "\n".join(round_lines) if round_lines else "No completed rounds yet."
        stop_text = stop_reason or "none"
        check_text = summarize_checks(latest_checks)
        if mode == PLANNER_MODE_RECORD:
            mode_guidance = (
                "Planner mode: record-only.\n"
                "Do not propose automatic follow-up execution.\n"
                "Always set should_propose_follow_up=false and leave suggested_next_objective empty.\n"
                "Focus on architecture notes, workstream tracking, TODO maintenance, and explorer backlog.\n\n"
            )
        else:
            mode_guidance = (
                "Planner mode: auto.\n"
                "You may propose one concrete follow-up objective when it should run as a new session.\n\n"
            )
        return (
            "You are the planning manager sub-agent for a Codex autoloop run.\n"
            "Your job is to maintain the implementation framework, identify what is complete, "
            "what remains, what should be explored next, and what the next executable objective should be.\n"
            "Use the local $planner-manager-explorer skill if it exists.\n\n"
            "**Length constraints (strictly enforce):**\n"
            "- Keep `summary` under 500 characters\n"
            "- Keep `evidence` (in workstreams) under 300 characters\n"
            "- Keep `next_step` (in workstreams) under 200 characters\n"
            "- Keep `suggested_next_objective` under 300 characters\n"
            "- Use concise phrases, not full sentences\n"
            "- Avoid code blocks and lengthy explanations\n\n"
            f"{mode_guidance}"
            "Strict rules:\n"
            "1) Work in read-only mode. Inspect the repository if useful, but do not modify files.\n"
            "2) Maintain a concrete workstream table. Do not output vague goals.\n"
            "3) Infer missing framework work if the main agent did not explicitly list it, but stay grounded in the repo state.\n"
            "4) Behave like an explorer-architect, not a passive summarizer.\n"
            "5) If external knowledge is missing, browse official docs, trusted APIs, datasets, or strong open-source references.\n"
            "6) Capture worthwhile exploration leads as concrete items, for example missing data sources, API integrations, or comparable projects.\n"
            "7) If the current session is terminal, propose one follow-up objective that can be executed as a new session.\n"
            "8) If no good follow-up exists yet, set should_propose_follow_up=false and leave suggested_next_objective empty.\n\n"
            f"Trigger: {trigger}\n"
            f"Terminal session: {terminal}\n"
            f"Stop reason: {stop_text}\n"
            f"Round index: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n\n"
            f"Objective:\n{objective}\n\n"
            "Operator messages:\n"
            f"{operator_text}\n\n"
            "Latest reviewer decision:\n"
            f"{review_text}\n\n"
            "Latest acceptance checks:\n"
            f"{check_text}\n\n"
            "Recent round history:\n"
            f"{rounds_text}\n"
        )

    def _fallback_snapshot(
        self,
        *,
        objective: str,
        latest_review: ReviewDecision | None,
        latest_checks: list[CheckResult],
        trigger: str,
        terminal: bool,
        error: str,
    ) -> PlanSnapshot:
        review_hint = latest_review.reason if latest_review is not None else "Planner did not return valid JSON."
        snapshot = PlanSnapshot(
            plan_id=uuid4().hex[:12],
            generated_at=datetime.now(timezone.utc).isoformat(),
            trigger=trigger,
            terminal=terminal,
            summary="Planner fallback snapshot due to invalid manager output.",
            workstreams=[
                PlanWorkstream(
                    area="Current objective",
                    status="in_progress" if not terminal else "todo",
                    evidence=objective[:300],
                    next_step=review_hint[:300],
                )
            ],
            done_items=[],
            remaining_items=[review_hint[:300] or "Re-run planner with clearer context."],
            risks=[error[:300]],
            next_steps=[review_hint[:300] or "Continue with the latest reviewer instruction."],
            exploration_items=[],
            suggested_next_objective="",
            should_propose_follow_up=False,
        )
        snapshot.report_markdown = format_plan_markdown(
            objective=objective,
            snapshot=snapshot,
            review=latest_review,
            checks=latest_checks,
            stop_reason=error,
        )
        return snapshot


def _strip_markdown_fences(text: str) -> str:
    """Strip leading/trailing markdown code fences (```json ... ``` or ``` ... ```)."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    start = 1  # skip the opening fence line
    end = len(lines)
    if lines[-1].strip() == "```":
        end = len(lines) - 1
    return "\n".join(lines[start:end]).strip()


def _find_plan_in_messages(messages: list[str]) -> PlanSnapshot | None:
    """Search agent_messages in reverse for the first parseable PlanSnapshot.
    Falls back to trying the full concatenated blob so partial/split output
    can still be rescued via the {…} extraction in parse_plan_text."""
    for msg in reversed(messages):
        result = parse_plan_text(msg)
        if result is not None:
            return result
    if len(messages) > 1:
        return parse_plan_text("\n".join(messages))
    return None


def parse_plan_text(text: str) -> PlanSnapshot | None:
    candidate = _strip_markdown_fences(text.strip())
    parsed = _load_json(candidate)
    if parsed is None:
        left = candidate.find("{")
        right = candidate.rfind("}")
        if left >= 0 and right > left:
            parsed = _load_json(candidate[left : right + 1])
    if parsed is None:
        return None

    required_fields = {
        "summary",
        "workstreams",
        "done_items",
        "remaining_items",
        "risks",
        "next_steps",
        "exploration_items",
        "suggested_next_objective",
        "should_propose_follow_up",
    }
    if not required_fields.issubset(parsed):
        return None

    summary = _as_text(parsed.get("summary"))
    if not summary:
        return None
    workstreams = _parse_workstreams(parsed.get("workstreams"))
    if workstreams is None:
        return None

    done_items = _parse_string_list(parsed.get("done_items"))
    remaining_items = _parse_string_list(parsed.get("remaining_items"))
    risks = _parse_string_list(parsed.get("risks"))
    next_steps = _parse_string_list(parsed.get("next_steps"))
    exploration_items = _parse_string_list(parsed.get("exploration_items"))
    if any(item is None for item in [done_items, remaining_items, risks, next_steps, exploration_items]):
        return None

    should_propose_follow_up = parsed.get("should_propose_follow_up")
    if not isinstance(should_propose_follow_up, bool):
        return None
    suggested_next_objective = _as_text(parsed.get("suggested_next_objective"))
    if should_propose_follow_up and not suggested_next_objective:
        return None
    if not should_propose_follow_up:
        suggested_next_objective = ""

    return PlanSnapshot(
        plan_id="",
        generated_at="",
        trigger="manual",
        terminal=False,
        summary=summary,
        workstreams=workstreams,
        done_items=done_items or [],
        remaining_items=remaining_items or [],
        risks=risks or [],
        next_steps=next_steps or [],
        exploration_items=exploration_items or [],
        suggested_next_objective=suggested_next_objective,
        should_propose_follow_up=should_propose_follow_up,
    )


def format_plan_markdown(
    *,
    objective: str,
    snapshot: PlanSnapshot,
    review: ReviewDecision | None,
    checks: list[CheckResult],
    stop_reason: str | None,
) -> str:
    lines = [
        "# Planning Snapshot",
        "",
        f"- Trigger: {snapshot.trigger}",
        f"- Terminal: {snapshot.terminal}",
        f"- Generated At (UTC): {snapshot.generated_at}",
        f"- Plan ID: {snapshot.plan_id}",
        "",
        "## Objective",
        objective.strip(),
        "",
        "## Manager Summary",
        snapshot.summary.strip(),
        "",
        "## Workstreams",
        "| Area | Status | Evidence | Next Step |",
        "| --- | --- | --- | --- |",
    ]
    for item in snapshot.workstreams:
        lines.append(
            f"| {_escape_table(item.area)} | {_escape_table(item.status)} | "
            f"{_escape_table(item.evidence)} | {_escape_table(item.next_step)} |"
        )
    lines.extend(_markdown_list("Completed", snapshot.done_items))
    lines.extend(_markdown_list("Remaining", snapshot.remaining_items))
    lines.extend(_markdown_list("Risks", snapshot.risks))
    lines.extend(_markdown_list("Recommended Next Steps", snapshot.next_steps))
    lines.extend(_markdown_list("Explorer Backlog", snapshot.exploration_items))
    if review is not None:
        lines.extend(
            [
                "## Reviewer",
                f"- Status: {review.status}",
                f"- Confidence: {review.confidence}",
                f"- Reason: {review.reason}",
                f"- Next Action: {review.next_action}",
                "",
            ]
        )
    lines.extend(
        [
            "## Acceptance Checks",
            summarize_checks(checks).strip(),
            "",
        ]
    )
    if stop_reason:
        lines.extend(["## Session Stop Reason", stop_reason.strip(), ""])
    if snapshot.should_propose_follow_up and snapshot.suggested_next_objective:
        lines.extend(
            [
                "## Suggested Next Objective",
                snapshot.suggested_next_objective.strip(),
                "",
            ]
        )
    else:
        lines.extend(["## Suggested Next Objective", "No follow-up objective proposed yet.", ""])
    return "\n".join(lines).strip() + "\n"


def format_plan_todo_markdown(*, objective: str, snapshot: PlanSnapshot) -> str:
    lines = [
        "# Planner TODO Board",
        "",
        f"- Objective: {objective.strip()}",
        f"- Updated At (UTC): {snapshot.generated_at}",
        f"- Plan ID: {snapshot.plan_id}",
        "",
        "## Summary",
        snapshot.summary.strip(),
        "",
        "## Workstream Table",
        "| Area | Status | Evidence | Next Step |",
        "| --- | --- | --- | --- |",
    ]
    for item in snapshot.workstreams:
        lines.append(
            f"| {_escape_table(item.area)} | {_escape_table(item.status)} | "
            f"{_escape_table(item.evidence)} | {_escape_table(item.next_step)} |"
        )
    lines.extend(_markdown_list("Priority Queue", snapshot.next_steps))
    lines.extend(_markdown_list("Remaining", snapshot.remaining_items))
    lines.extend(_markdown_list("Explorer Backlog", snapshot.exploration_items))
    if snapshot.should_propose_follow_up and snapshot.suggested_next_objective:
        lines.extend(["## Proposed Next Session", snapshot.suggested_next_objective.strip(), ""])
    return "\n".join(lines).strip() + "\n"


def _load_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _parse_workstreams(value: object) -> list[PlanWorkstream] | None:
    if not isinstance(value, list):
        return None
    out: list[PlanWorkstream] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            return None
        status = _normalize_workstream_status(item.get("status"))
        if status not in {"done", "in_progress", "todo", "blocked"}:
            return None
        # accept "name" as fallback for "area" (models sometimes use the wrong key)
        area = _as_text(item.get("area")) or _as_text(item.get("name"))
        evidence = _as_text(item.get("evidence"))
        next_step = _as_text(item.get("next_step"))
        if not area:
            return None
        out.append(
            PlanWorkstream(
                area=area,
                status=status,
                evidence=evidence,
                next_step=next_step,
            )
        )
    return out


def _parse_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value[:10]:
        text = _as_text(item)
        if text:
            out.append(text)
    return out


def _as_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_workstream_status(value: object) -> str:
    status = _as_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "complete": "done",
        "completed": "done",
        "inprogress": "in_progress",
        "in_progress": "in_progress",
    }
    return aliases.get(status, status)


def _first_non_empty(items: list[str]) -> str:
    for item in items:
        text = item.strip()
        if text:
            return text
    return ""


def _escape_table(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def _markdown_list(title: str, items: list[str]) -> list[str]:
    lines = [f"## {title}"]
    if not items:
        lines.append("- none")
    else:
        for item in items:
            lines.append(f"- {item}")
    lines.append("")
    return lines
