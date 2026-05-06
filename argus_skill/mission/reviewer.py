"""MissionReviewer — clean, neutral reviewer for ``MissionLoopEngine``.

Differences from upstream ``codex_autoloop.reviewer.Reviewer`` and from
argus-skill's older ``argus_skill/engineer/reviewer.py``:

- **No "concrete repository action" guard.** Upstream's reviewer downgrades
  any reply that doesn't run a shell command into ``continue``. That's
  right for a SWE-bench harness but wrong for a general-purpose agent
  whose objective may legitimately be answered with prose.
- **No DONE/REMAINING/BLOCKERS literal-string detection.** That template
  is upstream's contract, not ours.
- **No "generic role acknowledgment" pattern matcher.** Same reason.

The reviewer's job here is simpler:

  Given the objective and the main agent's last message, decide
  whether the objective is now satisfied. If yes → done. If clearly
  blocked (the agent said it needs more user input) → blocked. Otherwise
  → continue, and say *why*, with a concrete next_action.

We still emit upstream's ``ReviewDecision`` dataclass so existing
mission-runtime / state_store / chat_app machinery keeps working.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.models import CheckResult, ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..engineer.checks import summarize_checks


@dataclass
class MissionReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = True
    full_auto: bool = True
    dangerous_yolo: bool = False


class MissionReviewer:
    """One ``evaluate(...)`` call per round. Stateless across rounds."""

    def __init__(self, runner: RunnerBackend) -> None:
        self.runner = runner

    def evaluate(
        self,
        *,
        objective: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
        config: MissionReviewerConfig,
        operator_messages: list[str] | None = None,
        planner_review_instruction: str = "",
        verification_context: dict | None = None,
        active_skill_id: str | None = None,
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
            verification_context=verification_context or {},
            active_skill_id=active_skill_id,
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
            run_label="reviewer",
        )
        agent_messages = list(result.agent_messages or [])
        if not agent_messages:
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason=f"Reviewer returned empty output (exit={result.exit_code}).",
                next_action="Continue and produce a clearer summary of what was done.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
            )
        parsed = _find_decision_in_messages(agent_messages)
        if parsed is None:
            # The reviewer did say something, but not in the JSON shape we
            # asked for. Treat the whole final message as a soft "continue"
            # with the reviewer's freeform reason — no auto-coercion to
            # a punitive "you didn't run commands" verdict.
            tail = agent_messages[-1].strip()[:300]
            return ReviewDecision(
                status="continue",
                confidence=0.2,
                reason=f"Reviewer output was not valid JSON; raw: {tail}",
                next_action="Continue and produce a concise summary that addresses the objective.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output not parseable.\n",
            )
        return parsed

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        *,
        objective: str,
        operator_messages: list[str],
        planner_review_instruction: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        checks: list[CheckResult],
        verification_context: dict | None = None,
        active_skill_id: str | None = None,
    ) -> str:
        error_text = main_error or "none"
        check_text = summarize_checks(checks) if checks else "(no acceptance checks configured)"
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        verification_block = _format_verification_context(verification_context or {})
        skill_block = (
            f"Active skill id (if any): {active_skill_id}\n"
            if active_skill_id
            else "Active skill id: none\n"
        )
        return (
            "You are the reviewer sub-agent for an argus-skill mission.\n"
            "Decide whether the objective is now satisfied by reading the\n"
            "objective and the main agent's last reply. Return JSON only,\n"
            "no markdown fences.\n\n"
            "Required JSON keys (all strings unless noted):\n"
            "- status: one of \"done\", \"continue\", \"blocked\"\n"
            "- confidence: number in [0, 1]\n"
            "- reason: one short sentence\n"
            "- next_action: one short sentence; if status==done this can be\n"
            "  \"No further action needed.\"\n"
            "- round_summary_markdown: a structured markdown recap (bullet\n"
            "  list ok). No length limit — include everything that helps\n"
            "  the engineer act on the next round.\n"
            "- completion_summary_markdown: only for status==done; quote the\n"
            "  evidence that establishes the work is done. Otherwise empty.\n\n"
            "Optional JSON keys (fill ONLY when status != done):\n"
            "- failure_cause: one of\n"
            "  \"skill_gap\"          — the active skill's playbook is\n"
            "                          missing or wrong for this objective.\n"
            "  \"execution_mistake\"  — engineer made a one-off mistake the\n"
            "                          skill already covers.\n"
            "  \"environment\"        — missing dependency / quota / network /\n"
            "                          permissions; not the skill's fault.\n"
            "  \"flaky_check\"        — the verification command itself is\n"
            "                          unreliable or measuring the wrong\n"
            "                          thing.\n"
            "  \"unclear_objective\"  — the objective is ambiguous; need\n"
            "                          operator clarification.\n"
            "  \"unknown\"            — cause is genuinely unclear.\n"
            "- mission_lesson: a short paragraph (≤ 600 chars) addressed to\n"
            "  the engineer. ONLY emit this when failure_cause == \"skill_gap\"\n"
            "  AND you can name a *generalizable* principle (not a one-off\n"
            "  fix for this exact task). Phrase as guidance, not as code.\n"
            "  Examples of GOOD lessons: \"When parsing CSV, prefer the\n"
            "  `csv` module over manual `str.split` to handle quoted fields.\"\n"
            "  Examples of BAD lessons (do NOT emit these): \"Set TIMEOUT=42\n"
            "  in `main.py` line 17.\" — that is task-specific, not a skill\n"
            "  lesson.\n"
            "- verification_summary: ≤ 300 chars summarising what the raw\n"
            "  verification evidence shows.\n\n"
            "Decision rules (general-purpose, NOT SWE-bench):\n"
            "1) The objective sets the bar. A conversational objective is\n"
            "   satisfied by a sensible reply in the user's language; a\n"
            "   coding objective is satisfied by code/output evidence; an\n"
            "   explanation objective is satisfied by a correct explanation.\n"
            "   Use your judgement — do NOT demand shell commands or file\n"
            "   edits when the objective doesn't call for them.\n"
            "2) Choose `continue` when the main agent's reply is clearly\n"
            "   incomplete, off-topic, or wrong for the objective. Say what\n"
            "   is missing in `next_action`.\n"
            "3) Choose `blocked` ONLY when the main agent itself asked the\n"
            "   user a clarifying question, or hit a hard external blocker\n"
            "   (quota, missing credentials, etc.) that the operator must\n"
            "   resolve before progress is possible.\n"
            "4) If acceptance checks are configured and any failed, that\n"
            "   alone is enough to choose `continue`. Use the raw\n"
            "   verification evidence below — not just the engineer's\n"
            "   prose summary — to set `failure_cause`.\n"
            "5) When uncertain about failure_cause, pick \"unknown\" and\n"
            "   leave `mission_lesson` empty. Do NOT mutate skills on\n"
            "   guesswork.\n\n"
            f"Objective:\n{objective}\n\n"
            "Operator message history:\n"
            f"{operator_text}\n\n"
            "Planner guidance for this review (may be empty):\n"
            f"{planner_review_instruction or 'none'}\n\n"
            f"Round: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n"
            f"{skill_block}"
            f"Main agent fatal error: {error_text}\n\n"
            "Main agent last reply:\n"
            f"{main_summary or '(empty)'}\n\n"
            "Acceptance check results:\n"
            f"{check_text}\n\n"
            f"{verification_block}"
        )


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE | re.MULTILINE)
_VALID_STATUS = {"done", "continue", "blocked"}
_VALID_FAILURE_CAUSE = {
    "skill_gap",
    "execution_mistake",
    "environment",
    "flaky_check",
    "unclear_objective",
    "unknown",
}
_MAX_LESSON_CHARS = 1200
_MAX_VSUMMARY_CHARS = 600


def _format_verification_context(ctx: dict) -> str:
    """Render verification context for the reviewer prompt.

    ``ctx`` keys (all optional):
      - ``cmd``: command that produced the failure (or task id)
      - ``exit_code``: int
      - ``stdout_tail``: str
      - ``stderr_tail``: str
      - ``engineer_last_msg``: str (already in main_summary, but echoed
        here so the reviewer sees it next to the raw evidence)
      - ``notes``: extra context (e.g. "verify_cmd not configured; using
        engineer self-report")

    If ``ctx`` is empty we still emit a header so the reviewer knows it
    has no extra evidence to lean on.
    """
    if not ctx:
        return (
            "Raw verification evidence:\n"
            "(none — verification did not run or no extra evidence captured)\n"
        )
    parts = ["Raw verification evidence:"]
    cmd = (ctx.get("cmd") or "").strip()
    if cmd:
        parts.append(f"- command: {cmd}")
    if "exit_code" in ctx and ctx["exit_code"] is not None:
        parts.append(f"- exit_code: {ctx['exit_code']}")
    notes = (ctx.get("notes") or "").strip()
    if notes:
        parts.append(f"- notes: {notes}")
    stdout_tail = (ctx.get("stdout_tail") or "").strip()
    if stdout_tail:
        parts.append("- stdout (tail):\n" + _indent_block(stdout_tail))
    stderr_tail = (ctx.get("stderr_tail") or "").strip()
    if stderr_tail:
        parts.append("- stderr (tail):\n" + _indent_block(stderr_tail))
    engineer_last = (ctx.get("engineer_last_msg") or "").strip()
    if engineer_last:
        parts.append("- engineer self-report (verbatim):\n" + _indent_block(engineer_last))
    runtime_probe = (ctx.get("runtime_probe") or "").strip()
    if runtime_probe:
        parts.append(
            "- runtime probe (independent post-round container state — "
            "compare against engineer self-report; if they disagree, "
            "trust this):\n" + _indent_block(runtime_probe))
    acceptance_tests = ctx.get("acceptance_tests") or []
    if acceptance_tests:
        lines = "\n".join(f"      - {t}" for t in acceptance_tests)
        parts.append(
            "- official acceptance tests (these named tests currently FAIL "
            "and must PASS for the task to be considered resolved — this "
            "list is part of the task spec, not engineer-provided). The "
            "engineer's self-report is NOT sufficient: require evidence "
            "in the transcript that **each** of these specific tests was "
            "executed and reported PASS. If any are missing or ambiguous, "
            "return status=continue with next_action telling the engineer "
            "to run exactly these tests:\n" + lines
        )
    if "verify_exit" in ctx and ctx["verify_exit"] is not None:
        v_exit = ctx["verify_exit"]
        v_cmd = (ctx.get("verify_cmd") or "").strip()
        verdict = "PASS" if v_exit == 0 else "FAIL"
        header = (
            f"- official verifier ({verdict}, exit={v_exit}"
            + (f", cmd: {v_cmd}" if v_cmd else "")
            + ") — this is the **ground truth** from the task's "
              "official tests. When this disagrees with the engineer's "
              "self-report, trust this and not the engineer."
        )
        parts.append(header)
        v_out = (ctx.get("verify_stdout_tail") or "").strip()
        if v_out:
            parts.append("    verifier stdout (tail):\n" + _indent_block(v_out))
        v_err = (ctx.get("verify_stderr_tail") or "").strip()
        if v_err:
            parts.append("    verifier stderr (tail):\n" + _indent_block(v_err))
    return "\n".join(parts) + "\n"


def _indent_block(text: str, max_chars: int | None = None) -> str:
    """Indent a multi-line block. ``max_chars`` is now advisory only — we
    do not truncate by default since the engineer/reviewer prompts need
    full context. Pass an int explicitly to opt back into truncation.
    """
    if not text:
        return ""
    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = "...(truncated)...\n" + text[-max_chars:]
    return "\n".join("    " + line for line in text.splitlines()) or "    (empty)"


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _find_decision_in_messages(messages: list[str]) -> ReviewDecision | None:
    """Walk messages in reverse looking for the first parseable JSON verdict."""
    for msg in reversed(messages):
        decision = parse_decision_text(msg)
        if decision is not None:
            return decision
    return None


def parse_decision_text(text: str) -> ReviewDecision | None:
    if not text or not text.strip():
        return None
    candidate = _strip_fences(text)
    # Try whole-string parse first; if that fails, try to locate the
    # first {...} block and parse that.
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

    status = str(obj.get("status", "")).strip().lower()
    if status not in _VALID_STATUS:
        return None

    try:
        confidence = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reason = str(obj.get("reason") or obj.get("message") or "").strip()
    next_action = str(obj.get("next_action") or "").strip()
    if not next_action:
        if status == "done":
            next_action = "No further action needed."
        elif status == "blocked":
            next_action = "Operator input required before continuing."
        else:
            next_action = "Continue and address the gap noted in `reason`."

    round_summary = str(obj.get("round_summary_markdown") or "").strip()
    completion_summary = str(obj.get("completion_summary_markdown") or "").strip()

    failure_cause = str(obj.get("failure_cause") or "").strip().lower()
    if failure_cause and failure_cause not in _VALID_FAILURE_CAUSE:
        failure_cause = "unknown"
    # Force-clear failure fields when reviewer says done — they only
    # apply to non-success rounds.
    if status == "done":
        failure_cause = ""
        mission_lesson = ""
    else:
        mission_lesson = str(obj.get("mission_lesson") or "").strip()
        # Only honour mission_lesson when failure_cause is skill_gap.
        # Reviewer prompt already enforces this; we double-gate at the
        # parser to avoid drift if a reviewer ignores the rule.
        if failure_cause != "skill_gap":
            mission_lesson = ""
        if len(mission_lesson) > _MAX_LESSON_CHARS:
            mission_lesson = mission_lesson[:_MAX_LESSON_CHARS].rstrip() + "..."

    verification_summary = str(obj.get("verification_summary") or "").strip()
    if len(verification_summary) > _MAX_VSUMMARY_CHARS:
        verification_summary = verification_summary[:_MAX_VSUMMARY_CHARS].rstrip() + "..."

    return ReviewDecision(
        status=status,
        confidence=confidence,
        reason=reason or f"Reviewer voted {status}.",
        next_action=next_action,
        round_summary_markdown=round_summary,
        completion_summary_markdown=completion_summary,
        failure_cause=failure_cause,
        mission_lesson=mission_lesson,
        verification_summary=verification_summary,
    )


__all__ = [
    "MissionReviewer",
    "MissionReviewerConfig",
    "parse_decision_text",
]
