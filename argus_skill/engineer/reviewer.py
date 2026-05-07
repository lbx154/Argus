"""Reviewer sub-agent: graded "done / continue / blocked" verdict.

Provenance: vendored from ``ArgusBot/codex_autoloop/reviewer.py``. The
substantive change is decoupling: the original took a ``CodexRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus_skill.core.ports``) so it works with codex, claude-code, or the
in-memory test stub equally well.

Public surface kept identical: ``Reviewer.evaluate(...) -> ReviewDecision``,
``parse_decision_text(text) -> ReviewDecision | None``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import CheckResult, ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from .checks import summarize_checks


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False


SCHEMA_PATH = str(Path(__file__).with_name("reviewer_schema.json"))


class Reviewer:
    """One reviewer call per round. Stateless across rounds."""

    def __init__(self, runner: RunnerBackend) -> None:
        self.runner = runner
        self.schema_path = SCHEMA_PATH

    def evaluate(
        self,
        *,
        objective: str,
        operator_messages: list[str] | None = None,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
        config: ReviewerConfig,
        planner_review_instruction: str = "",
    ) -> ReviewDecision:
        prompt = self._build_prompt(
            objective=objective,
            operator_messages=operator_messages or [],
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            checks=checks,
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
                extra_args=list(config.extra_args) if config.extra_args else None,
                output_schema_path=self.schema_path,
            ),
            run_label="reviewer",
        )
        if not result.agent_messages:
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
            )
        parsed = _find_decision_in_messages(result.agent_messages)
        if parsed is None:
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason="Reviewer output was not valid JSON.",
                next_action="Continue implementation and include clear completion evidence.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output was not valid JSON.\n",
            )
        return _coerce_decision_against_main_summary(parsed, main_summary=main_summary)

    def _build_prompt(
        self,
        *,
        objective: str,
        operator_messages: list[str],
        planner_review_instruction: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
    ) -> str:
        error_text = main_error or "none"
        check_text = summarize_checks(checks)
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        return (
            "You are the reviewer sub-agent for an argus-skill autoloop run.\n"
            "Decide whether the objective is fully complete.\n\n"
            "Return valid JSON matching the provided schema.\n"
            "Do not wrap the response in markdown fences.\n\n"
            "**Length constraints (strictly enforce):**\n"
            "- Keep `round_summary_markdown` concise (under 2000 characters)\n"
            "- Keep `completion_summary_markdown` under 1500 characters\n"
            "- Avoid code blocks in summaries - focus on outcomes, not implementation details\n"
            "- Use brief bullet points, not lengthy explanations\n\n"
            "Required JSON keys:\n"
            "- status\n"
            "- confidence\n"
            "- reason\n"
            "- next_action\n"
            "- round_summary_markdown\n"
            "- completion_summary_markdown\n\n"
            "Decision rules:\n"
            "1) Choose `done` ONLY when the main agent's last summary contains\n"
            "   CONCRETE EVIDENCE that the work succeeded: actual command output,\n"
            "   test results, file inspections with shown contents, query\n"
            "   results, or other artifact you can read. A bare assertion such\n"
            "   as `I implemented X` or `Verified that file Y exists` WITHOUT\n"
            "   showing the actual command + output is NOT evidence.\n"
            "2) Default to `continue` whenever the agent's claims are not backed\n"
            "   by concrete artifacts in the summary. Better to spend another\n"
            "   round verifying than declare premature `done`. The agent has no\n"
            "   ground-truth signal — your job is to demand evidence.\n"
            "3) When `continue`, `next_action` must be a concrete instruction\n"
            "   that asks for SPECIFIC verification commands (e.g.,\n"
            "   `run pytest -xvs and paste the full output`,\n"
            "   `cat the produced file and show first 50 lines`,\n"
            "   `run the SPARQL query and show the returned rows`).\n"
            "4) Use `blocked` ONLY when additional user input is strictly\n"
            "   required to make ANY further progress (e.g. missing\n"
            "   credentials, ambiguous spec the user must clarify, hardware\n"
            "   the agent cannot access). A failing test, a runtime error,\n"
            "   incorrect output, or any other condition the agent COULD\n"
            "   attempt to fix on its own is NOT `blocked` — it is\n"
            "   `continue` with a concrete next_action telling the agent\n"
            "   what to debug. When in doubt, prefer `continue` over\n"
            "   `blocked`. Example: tests still failing → `continue`, NOT\n"
            "   `blocked`. Example: file not yet created → `continue`,\n"
            "   NOT `blocked`.\n"
            "5) `round_summary_markdown` summarizes this round's completed work,\n"
            "   evidence shown, and remaining gaps.\n"
            "6) If status is not `done`, `completion_summary_markdown` should be\n"
            "   a short placeholder or empty note.\n"
            "7) If status is `done`, `completion_summary_markdown` must quote\n"
            "   the concrete evidence (command + output) that establishes\n"
            "   success. No evidence → not done.\n\n"
            f"Objective:\n{objective}\n\n"
            "Operator message history (source of truth for user instructions):\n"
            f"{operator_text}\n\n"
            "Planner guidance for this review:\n"
            f"{planner_review_instruction or 'none'}\n\n"
            f"Round: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n"
            f"Main agent fatal error: {error_text}\n\n"
            "Main agent last summary:\n"
            f"{main_summary}\n\n"
            "Acceptance check results:\n"
            f"{check_text}\n"
        )


# ---------------------------------------------------------------------------
# Parsing helpers (kept module-level so callers can unit-test parsing
# without spinning up a runner). Verbatim from ArgusBot.
# ---------------------------------------------------------------------------

def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    start = 1
    end = len(lines)
    if lines[-1].strip() == "```":
        end = len(lines) - 1
    return "\n".join(lines[start:end]).strip()


def _find_decision_in_messages(messages: list[str]) -> "ReviewDecision | None":
    for msg in reversed(messages):
        result = parse_decision_text(msg)
        if result is not None:
            return result
    if len(messages) > 1:
        return parse_decision_text("\n".join(messages))
    return None


def parse_decision_text(text: str) -> ReviewDecision | None:
    candidate = _strip_markdown_fences(text.strip())
    parsed = _load_json(candidate)
    if parsed is None:
        left = candidate.find("{")
        right = candidate.rfind("}")
        if left >= 0 and right > left:
            parsed = _load_json(candidate[left : right + 1])
    if parsed is None:
        return None
    status = _parse_status(parsed)
    if status not in {"done", "continue", "blocked"}:
        return None
    confidence = _parse_confidence(parsed.get("confidence"))
    round_summary_markdown = _parse_round_summary(parsed)
    reason = _parse_reason(parsed, round_summary_markdown=round_summary_markdown)
    next_action = _parse_next_action(parsed, status=status)
    completion_summary_markdown = _parse_optional_text(parsed.get("completion_summary_markdown"))
    if any(
        item is None
        for item in [
            confidence,
            reason,
            next_action,
            round_summary_markdown,
            completion_summary_markdown,
        ]
    ):
        return None
    return ReviewDecision(
        status=status,
        confidence=confidence,
        reason=reason,
        next_action=next_action,
        round_summary_markdown=round_summary_markdown,
        completion_summary_markdown=completion_summary_markdown,
    )


def _load_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _parse_status(parsed: dict) -> str | None:
    for key in ("status", "decision", "action"):
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in {"done", "continue", "blocked"}:
            return normalized
    return None


def _parse_confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def _parse_required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _parse_reason(parsed: dict, *, round_summary_markdown: str | None) -> str | None:
    for key in ("reason", "message"):
        text = _parse_required_text(parsed.get(key))
        if text is not None:
            return text
    derived = _derive_reason_from_markdown(
        _parse_optional_text(parsed.get("completion_summary_markdown"))
        or round_summary_markdown
        or ""
    )
    return derived


def _parse_next_action(parsed: dict, *, status: str) -> str | None:
    direct = _parse_required_text(parsed.get("next_action"))
    if direct is not None:
        return direct
    if status == "done":
        return "No further action needed. Objective complete."
    if status == "blocked":
        return "Need additional user input before continuing."
    if status == "continue":
        return "Continue implementation and include clear completion evidence."
    return None


def _parse_round_summary(parsed: dict) -> str | None:
    direct = _parse_required_text(parsed.get("round_summary_markdown"))
    if direct is not None:
        return direct
    summary = _parse_required_text(parsed.get("summary")) or _parse_required_text(parsed.get("message"))
    if summary is None:
        return None
    return f"# Review Summary\n\n- {summary}\n"


def _parse_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()


def _derive_reason_from_markdown(text: str) -> str | None:
    normalized_lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith("**") and line.endswith("**") and len(line) > 4:
            line = line[2:-2].strip()
        normalized_lines.append(line)
    if not normalized_lines:
        return None
    candidate = normalized_lines[0]
    return candidate[:300].strip() or None


GENERIC_MAIN_PATTERNS = [
    "i am the primary implementation agent",
    "i'm the primary implementation agent",
    "i\u2019m the primary implementation agent",
    "i will act as the primary implementation agent",
    "i'll act as the primary implementation agent",
    "i\u2019ll act as the primary implementation agent",
    "acting as the primary implementation agent",
    "i'll handle the main task directly",
    "i\u2019ll handle the main task directly",
    "continuing as the primary implementation agent",
    "i\u2019ll keep ownership of the main task here",
    "i'll keep ownership of the main task here",
]

CONCRETE_EXECUTION_PATTERNS = [
    "done:",
    "remaining:",
    "blockers:",
]

COMMAND_EVIDENCE_RE = re.compile(r"\b(?:ran|executed)\s+(?:pytest|git diff|git status|rg|get-content)\b")
COMPLETED_ACTION_RE = re.compile(
    r"\b(?:read|inspected|edited|updated|changed|patched|ran|tested|implemented|verified|fixed)\b"
)


def _coerce_decision_against_main_summary(
    decision: ReviewDecision, *, main_summary: str
) -> ReviewDecision:
    normalized = " ".join((main_summary or "").lower().split())
    if any(pattern in normalized for pattern in GENERIC_MAIN_PATTERNS) and not _has_concrete_execution_evidence(
        main_summary
    ):
        return ReviewDecision(
            status="continue",
            confidence=min(decision.confidence, 0.2),
            reason=(
                "Main agent summary appears to be a generic role acknowledgment without concrete repository work. "
                "Continue and require specific execution evidence."
            ),
            next_action="Perform concrete repository inspection or code changes before the next review.",
            round_summary_markdown=(
                decision.round_summary_markdown
                or "# Review Summary\n\n- Main summary was a generic acknowledgment without concrete execution evidence.\n"
            ),
            completion_summary_markdown="",
        )
    return decision


def _has_concrete_execution_evidence(summary: str) -> bool:
    text = summary or ""
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False
    if any(pattern in normalized for pattern in CONCRETE_EXECUTION_PATTERNS):
        return True
    if COMMAND_EVIDENCE_RE.search(normalized):
        return True
    return COMPLETED_ACTION_RE.search(normalized) is not None
