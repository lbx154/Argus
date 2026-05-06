"""Prompts used by ``MissionLoopEngine``.

Design rules:

- **Neutral, no classification.** The engineer is a general-purpose agent
  given an objective. We do not pre-classify the task as "chat / file /
  question"; we don't tell the engineer it MUST run a command, MUST write
  a file, MUST end with a particular template. We trust the engineer to
  pick the right shape from the objective text.
- **Round 1 is minimal.** Just the objective and any standing operator
  guidance. No "before finishing this turn, do at least one concrete
  repo action" mandate, no DONE/REMAINING/BLOCKERS template.
- **Continuation prompts carry only what's new.** Reviewer's reason +
  next-action, fresh operator messages, and the planner's main_instruction
  if a follow-up phase has begun.
- **Operator overrides are explicit.** When the operator interrupted a
  round with `/inject`, that instruction is shown verbatim.
- **Follow-up phase** is only entered after a successful round, plan_mode
  is auto, and the planner asked for one. We surface the planner's
  next_explore / main_instruction without dressing them up.
"""
from __future__ import annotations

from typing import Any


def _as_text(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def _format_operator_messages(messages: list[str] | None) -> str:
    items = [_as_text(m) for m in (messages or []) if _as_text(m)]
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    return (
        "## Standing operator messages\n"
        "Earlier instructions from the operator that still apply:\n"
        f"{body}\n"
    )


def initial_main_prompt(
    *,
    objective: str,
    operator_messages: list[str] | None = None,
    plan: Any = None,  # PlanDecision | None
) -> str:
    """First-round prompt. Keep it tight.

    We do not prepend "you are the primary implementation agent" with a
    list of mandates. The engineer's own system prompt (from the codex
    binary / argus-skill RunnerBackend) already tells it it's an agent;
    here we just hand over the objective.
    """
    parts: list[str] = []
    parts.append(f"## Objective\n{_as_text(objective)}")
    op_block = _format_operator_messages(operator_messages)
    if op_block:
        parts.append(op_block.rstrip())
    main_instruction = _as_text(getattr(plan, "main_instruction", "") if plan else "")
    if main_instruction:
        parts.append(
            "## Planner guidance\n"
            f"{main_instruction}"
        )
    parts.append(
        "## Output\n"
        "Address the objective directly. Choose whatever response shape\n"
        "fits the request — answer in chat, run commands, edit files,\n"
        "ask one clarifying question, or any combination. Use the\n"
        "user's language. Keep it as short as the task allows."
    )
    return "\n\n".join(parts)


def continue_main_prompt(
    *,
    objective: str,
    review: Any,  # ReviewDecision
    checks_ok: bool,
    operator_messages: list[str] | None = None,
    plan: Any = None,
    mission_lesson: str = "",
    verification_evidence: dict | None = None,
) -> str:
    """Round N>1 prompt after reviewer asked for another round.

    ``mission_lesson`` is reviewer-supplied "transient skill patch" — a
    short principle the engineer should apply on the upcoming round
    (Phase 1 reviewer→skill loop). It is NOT persisted to the skill
    file; it lives only in this prompt.

    ``verification_evidence`` is the raw failure evidence (cmd, exit
    code, stderr/stdout tails) from the previous round's check
    failure. We include it verbatim so the engineer reasons from facts,
    not from the reviewer's compressed reason.
    """
    parts: list[str] = [f"## Objective\n{_as_text(objective)}"]

    op_block = _format_operator_messages(operator_messages)
    if op_block:
        parts.append(op_block.rstrip())

    review_reason = _as_text(getattr(review, "reason", ""))
    next_action = _as_text(getattr(review, "next_action", ""))
    round_summary = _as_text(getattr(review, "round_summary_markdown", ""))
    feedback_lines: list[str] = []
    if review_reason:
        feedback_lines.append(f"**Reviewer reason:** {review_reason}")
    if next_action:
        feedback_lines.append(f"**Next action requested:** {next_action}")
    if not checks_ok:
        feedback_lines.append(
            "**Acceptance checks did not all pass** — re-run them and "
            "fix what's red."
        )
    if feedback_lines:
        parts.append("## Reviewer feedback from previous round\n" + "\n".join(feedback_lines))
    if round_summary:
        # Reviewer's structured recap of the previous round (already a
        # markdown bullet list). Forwarded verbatim — no length cap — so
        # the engineer sees the same level of detail the reviewer did.
        parts.append(
            "## Reviewer round summary (previous round)\n" + round_summary
        )

    lesson_text = _as_text(mission_lesson)
    if lesson_text:
        parts.append(
            "## Lesson for this mission (apply on this round)\n"
            "The reviewer identified a generalizable principle from the\n"
            "previous round. Apply it now. This lesson is mission-local\n"
            "and not part of any persisted skill.\n\n"
            f"{lesson_text}"
        )

    evidence_block = _format_verification_evidence(verification_evidence or {})
    if evidence_block:
        parts.append(evidence_block)

    main_instruction = _as_text(getattr(plan, "main_instruction", "") if plan else "")
    if main_instruction:
        parts.append(f"## Planner guidance\n{main_instruction}")

    parts.append(
        "## Output\n"
        "Make the requested adjustment and respond. Pick whatever\n"
        "response shape fits — chat reply, commands, file edits,\n"
        "or a clarification question."
    )
    return "\n\n".join(parts)


def _format_verification_evidence(evidence: dict) -> str:
    """Render raw verification evidence for the engineer's continue prompt.

    Forwards everything the reviewer saw: command/exit, full stdout/stderr
    tails (no length cap), in-container verifier summary, the still-failing
    acceptance test list, the engineer's own previous self-report (so it
    can compare its claims against ground truth), and any independent
    runtime probe output.
    """
    if not evidence:
        return ""
    lines: list[str] = []
    cmd = _as_text(evidence.get("cmd"))
    if cmd:
        lines.append(f"- command: `{cmd}`")
    if "exit_code" in evidence and evidence["exit_code"] is not None:
        lines.append(f"- exit_code: {evidence['exit_code']}")
    stdout_tail = _as_text(evidence.get("stdout_tail"))
    if stdout_tail:
        lines.append("- stdout (tail):\n```\n" + stdout_tail + "\n```")
    stderr_tail = _as_text(evidence.get("stderr_tail"))
    if stderr_tail:
        lines.append("- stderr (tail):\n```\n" + stderr_tail + "\n```")
    # In-container verifier summary (SWE-Bench-Pro path). Surfaces the
    # ground-truth acceptance set the engineer must turn green.
    acceptance_tests = evidence.get("acceptance_tests") or []
    if acceptance_tests:
        names = "\n".join(f"  - {t}" for t in acceptance_tests)
        lines.append(
            "- acceptance tests still FAILING (run each by name and "
            "report PASS):\n" + names
        )
    # Verifier exit + raw harness tails, if the runner attached them.
    if "verify_exit" in evidence and evidence["verify_exit"] is not None:
        v_exit = evidence["verify_exit"]
        v_cmd = _as_text(evidence.get("verify_cmd"))
        verdict = "PASS" if v_exit == 0 else "FAIL"
        head = f"- official verifier ({verdict}, exit={v_exit}"
        if v_cmd:
            head += f", cmd: `{v_cmd}`"
        head += ")"
        lines.append(head)
        v_out = _as_text(evidence.get("verify_stdout_tail"))
        if v_out:
            lines.append("  verifier stdout (tail):\n```\n" + v_out + "\n```")
        v_err = _as_text(evidence.get("verify_stderr_tail"))
        if v_err:
            lines.append("  verifier stderr (tail):\n```\n" + v_err + "\n```")
    engineer_last = _as_text(evidence.get("engineer_last_msg"))
    if engineer_last:
        lines.append(
            "- your previous self-report (verbatim — compare against the "
            "verifier above before claiming done):\n```\n"
            + engineer_last
            + "\n```"
        )
    runtime_probe = _as_text(evidence.get("runtime_probe"))
    if runtime_probe:
        lines.append(
            "- runtime probe (independent post-round container state):\n```\n"
            + runtime_probe
            + "\n```"
        )
    if not lines:
        return ""
    return (
        "## Verification evidence from previous round\n"
        "Use these raw outputs (not just the reviewer's prose) when\n"
        "deciding what to fix.\n\n"
        + "\n".join(lines)
    )


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return "...(truncated)...\n" + text[-max_chars:]


def operator_override_prompt(
    *,
    objective: str,
    instruction: str,
    operator_messages: list[str] | None = None,
    plan: Any = None,
) -> str:
    """Round prompt after the operator interrupted with /inject."""
    parts: list[str] = [f"## Objective\n{_as_text(objective)}"]

    op_block = _format_operator_messages(operator_messages)
    if op_block:
        parts.append(op_block.rstrip())

    parts.append(
        "## Operator interruption (apply now)\n"
        "The operator interrupted the previous round and gave this new\n"
        "instruction. Apply it before continuing the original objective:\n\n"
        f"{_as_text(instruction)}"
    )

    main_instruction = _as_text(getattr(plan, "main_instruction", "") if plan else "")
    if main_instruction:
        parts.append(f"## Planner guidance\n{main_instruction}")

    parts.append(
        "## Output\n"
        "Apply the operator's instruction and respond. Pick whatever\n"
        "response shape fits the work."
    )
    return "\n\n".join(parts)


def follow_up_prompt(
    *,
    objective: str,
    plan: Any,  # PlanDecision
    operator_messages: list[str] | None = None,
) -> str:
    """Prompt for the optional follow-up round (plan_mode=auto only)."""
    parts: list[str] = [
        f"## Original objective (now complete)\n{_as_text(objective)}",
    ]

    op_block = _format_operator_messages(operator_messages)
    if op_block:
        parts.append(op_block.rstrip())

    next_explore = _as_text(getattr(plan, "next_explore", ""))
    main_instruction = _as_text(getattr(plan, "main_instruction", ""))
    parts.append(
        "## Follow-up sub-objective (planner-proposed)\n"
        f"{main_instruction or next_explore or 'Continue exploring related leads.'}"
    )

    parts.append(
        "## Output\n"
        "Tackle the follow-up. Same rules as the main objective: pick\n"
        "the response shape that fits."
    )
    return "\n\n".join(parts)


__all__ = [
    "initial_main_prompt",
    "continue_main_prompt",
    "operator_override_prompt",
    "follow_up_prompt",
]
