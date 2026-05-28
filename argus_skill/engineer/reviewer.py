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
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from ..core.models import CheckResult, ReviewDecision, ReviewStatus, RunnerOptions
from ..core.ports import RunnerBackend
from ..skills.role_context import format_role_context, load_builtin_skill_text
from .checks import summarize_checks

log = logging.getLogger(__name__)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False


SCHEMA_PATH = str(Path(__file__).with_name("reviewer_schema.json"))
_REVIEWER_ROLE_SKILL = "argus-reviewer-role.md"
_REVIEWER_ENGINEER_HANDOFF_SKILL = "reviewer-engineer-handoff.md"
_ACADEMIC_PAPER_REVIEW_SKILL = "academic-paper-peer-review-benchmark.md"
_REVIEWER_ROLE_FALLBACK = """# Argus Reviewer Role

The Reviewer is argus-skill's evidence gate. Decide done/continue/blocked from
concrete artifacts and checks, and turn failures into concise engineer
next_action instructions.
"""
_REVIEWER_ENGINEER_HANDOFF_FALLBACK = """# Reviewer-to-engineer handoff

When validation fails, your `next_action` is the engineer's next prompt. The
engineer may be a smaller model, so convert logs into a concise repair brief:
name failed commands, exit codes, issue codes, exact paths, ordered fixes, and
the command that proves completion. Do not paste raw logs wholesale.
"""
_ACADEMIC_PAPER_REVIEW_FALLBACK = """# Academic paper peer-review benchmark

Use for nearly complete EMNLP/ACL paper tasks. Simulate a strict reviewer:
score contribution, claim-evidence alignment, experiment integrity, benchmark
quality, literature/citations, reproducibility, writing, format/layout, and the
strongest reviewer objection. Any remaining major actionable reviewer objection
means `continue`, not `done`.
"""


def _load_reviewer_engineer_handoff_skill() -> str:
    return load_builtin_skill_text(
        _REVIEWER_ENGINEER_HANDOFF_SKILL, _REVIEWER_ENGINEER_HANDOFF_FALLBACK
    )


def _load_academic_paper_review_skill() -> str:
    return load_builtin_skill_text(
        _ACADEMIC_PAPER_REVIEW_SKILL, _ACADEMIC_PAPER_REVIEW_FALLBACK
    )


def _format_academic_paper_review_skill_block(
    *,
    objective: str,
    operator_messages: list[str],
    planner_review_instruction: str,
    main_summary: str,
    active_skill_id: str | None,
    check_text: str,
    raw_evidence: str,
) -> str:
    if not _should_include_academic_paper_review_skill(
        objective=objective,
        operator_messages=operator_messages,
        planner_review_instruction=planner_review_instruction,
        main_summary=main_summary,
        active_skill_id=active_skill_id,
        check_text=check_text,
        raw_evidence=raw_evidence,
    ):
        return ""
    skill = _load_academic_paper_review_skill()
    return (
        "Academic-paper peer review benchmark skill "
        "(apply only to near-complete academic paper scopes):\n"
        f"{skill}\n\n"
    )


def _should_include_academic_paper_review_skill(
    *,
    objective: str,
    operator_messages: list[str],
    planner_review_instruction: str,
    main_summary: str,
    active_skill_id: str | None,
    check_text: str,
    raw_evidence: str,
) -> bool:
    context = "\n".join(
        [
            objective,
            "\n".join(operator_messages),
            planner_review_instruction,
            main_summary,
            active_skill_id or "",
            check_text,
            raw_evidence,
        ]
    ).lower()
    paper_markers = (
        "paper/main.tex",
        "paper/main.pdf",
        "main.pdf",
        "manuscript",
        "academic paper",
        "paper draft",
        "emnlp",
        "acl",
        "latex",
    )
    complete_markers = (
        "final_submission",
        "submission-ready",
        "publication quality",
        "validate-full-emnlp",
        "submission_assurance",
        "paper_draft_report",
        "format_preflight",
        "academic_language_review",
        "layout_review",
        "references.bib",
        "compiled pdf",
        "main.pdf",
    )
    return any(marker in context for marker in paper_markers) and any(
        marker in context for marker in complete_markers
    )


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
        active_skill_id: str | None = None,
        engineer_reasoning_summary: str = "",
        prev_review_summary: str = "",
        raw_evidence: str = "",
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
            active_skill_id=active_skill_id,
            engineer_reasoning_summary=engineer_reasoning_summary,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
        )
        try:
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
        except Exception as exc:  # noqa: BLE001
            msg = f"Reviewer runner raised {type(exc).__name__}: {exc}"
            log.exception("reviewer runner raised")
            return ReviewDecision(
                status="blocked",
                confidence=0.0,
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
            )
        rev_in = int(getattr(result, "input_tokens", 0) or 0)
        rev_cached = int(getattr(result, "cached_input_tokens", 0) or 0)
        rev_out = int(getattr(result, "output_tokens", 0) or 0)
        if not result.agent_messages:
            fatal = str(getattr(result, "fatal_error", "") or "").strip()
            if fatal or result.exit_code != 0:
                reason = (
                    "Reviewer backend returned no output "
                    f"(exit={result.exit_code}"
                    + (f", fatal_error={fatal}" if fatal else "")
                    + ")."
                )
                return ReviewDecision(
                    status="continue",
                    confidence=0.0,
                    reason=reason,
                    next_action=(
                        "Retry the reviewer after the backend recovers; do not treat "
                        "this as evidence that the engineer completed or failed the task."
                    ),
                    round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                    completion_summary_markdown="",
                    failure_cause="environmental",
                    input_tokens=rev_in,
                    cached_input_tokens=rev_cached,
                    output_tokens=rev_out,
                )
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
            )
        parsed = _find_decision_in_messages(result.agent_messages)
        if parsed is None:
            return ReviewDecision(
                status="continue",
                confidence=0.0,
                reason="Reviewer output was not valid JSON.",
                next_action="Continue implementation and include clear completion evidence.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output was not valid JSON.\n",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
            )
        # Phase-2 instrumentation: cost-tracking sinks (e.g. LifeSupervisor's
        # _CostTrackingSink) read these fields off ``round.review.completed``
        # events. If we don't propagate them every iteration budget enforcement
        # silently breaks and the journal shows ``cost_usd=$0.0000``.
        parsed.input_tokens = rev_in
        parsed.cached_input_tokens = rev_cached
        parsed.output_tokens = rev_out
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
        active_skill_id: str | None = None,
        engineer_reasoning_summary: str = "",
        prev_review_summary: str = "",
        raw_evidence: str = "",
    ) -> str:
        error_text = main_error or "none"
        check_text = summarize_checks(checks)
        reviewer_role_context = format_role_context(
            "Argus reviewer role skill",
            _REVIEWER_ROLE_SKILL,
            _REVIEWER_ROLE_FALLBACK,
        )
        handoff_skill = _load_reviewer_engineer_handoff_skill()
        paper_review_skill_block = _format_academic_paper_review_skill_block(
            objective=objective,
            operator_messages=operator_messages,
            planner_review_instruction=planner_review_instruction,
            main_summary=main_summary,
            active_skill_id=active_skill_id,
            check_text=check_text,
            raw_evidence=raw_evidence,
        )
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_full_pipeline_checklist,
            format_stage_checklist,
        )
        from pathlib import Path as _Path

        stage = current_stage(_Path.cwd())
        if stage == "submission":
            stage_checklist = format_full_pipeline_checklist(role="reviewer")
        else:
            stage_checklist = format_stage_checklist(stage, role="reviewer")

        # Stage-rollback instruction. When the reviewer notices that an
        # upstream stage's evidence is missing or unreliable while
        # working a later stage, demoting current_stage back to the
        # earlier stage is the correct move — the agent should not be
        # asked to repair upstream defects through the current stage's
        # acceptance criteria. The instruction lives here (not in the
        # individual checklist items) so it applies uniformly.
        stage_idx = (
            CANONICAL_STAGE_ORDER.index(stage)
            if stage in CANONICAL_STAGE_ORDER
            else 0
        )
        earlier_stages = ", ".join(CANONICAL_STAGE_ORDER[:stage_idx]) or "(none)"
        rollback_block = (
            "## Stage rollback\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "If you discover that an *earlier* stage's evidence is missing, "
            "stale, or unreliable (e.g. while in `run` you notice the "
            "`benchmark` evaluator is a stub; while in `draft` you notice "
            "that `research/INFRA_CHOICE.md` was never locked in; while in "
            "`analysis` you notice the `run.score_variance` rows are all "
            "identical) — do NOT try to patch the gap from inside the "
            "current stage. Reply `continue` and tell the engineer to roll "
            "back the pipeline state machine by calling:\n\n"
            "    python -c \"from argus_skill.skills.stage_checklists import rollback_stage; "
            "rollback_stage('.', target_stage='<earlier-stage>', "
            "reason='<one-sentence reason>')\"\n\n"
            "then complete the earlier stage's checklist before re-advancing."
        )
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        shared_context_block = _format_engineer_shared_context(
            skill_used=active_skill_id,
            engineer_reasoning_summary=engineer_reasoning_summary,
            prev_review_summary=prev_review_summary,
        )
        # v12 phase-4: when callers (e.g. harbor_adapter) collect richer
        # post-round evidence (engineer self-report verbatim, runtime probe,
        # official verifier output with "ground truth, trust this" framing),
        # they pass it as ``raw_evidence``. We append it after the
        # acceptance-check section so the reviewer always has the strongest
        # signal grounded in actual container state, not just the
        # engineer's prose. Empty string → legacy v3 behaviour.
        evidence_block = (
            f"\nRaw verification evidence:\n{raw_evidence.rstrip()}\n"
            if raw_evidence.strip()
            else ""
        )
        return (
            "You are the reviewer sub-agent for an argus-skill autoloop run.\n"
            "Decide whether the objective is fully complete.\n\n"
            "**You have shell access via your tools.** When the main agent's\n"
            "summary is missing verbatim verification output (pytest, ruff,\n"
            "mypy, file listing, etc.), do NOT default to `continue` —\n"
            "instead re-run the relevant commands yourself in the working\n"
            "directory and use *your own* output as ground truth. Only\n"
            "after you have ground truth do you decide. This costs 1 extra\n"
            "command but saves an entire engineer round.\n\n"
            "Return valid JSON matching the provided schema.\n"
            "Do not wrap the response in markdown fences.\n\n"
            f"{reviewer_role_context}"
            "Reviewer-to-engineer handoff skill:\n"
            f"{handoff_skill}\n\n"
            f"{paper_review_skill_block}"
            f"{stage_checklist}\n\n"
            f"{rollback_block}\n\n"
            "**Length constraints:**\n"
            "- Be thorough in `round_summary_markdown` — include all relevant details\n"
            "- Use brief bullet points, not lengthy explanations\n"
            "- `next_action` must contain ALL specific issues, failure details, and repair steps\n"
            "  the engineer needs — do NOT summarize away critical information\n\n"
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
            "1a) Symmetric stop rule: if the agent's last summary DOES include\n"
            "   verbatim command output (e.g. pytest pass count, script stdout,\n"
            "   `ls -l` showing the artifact) AND that output directly\n"
            "   satisfies the operator request, choose `done`. Do NOT demand\n"
            "   yet another round whose only purpose is re-running the same\n"
            "   commands and re-printing the same outputs. That wastes rounds\n"
            "   and tokens. Anti-pattern: agent shows test_accuracy=0.98 + the\n"
            "   classification report → reviewer says `continue` asking for\n"
            "   `run the script and paste the output` even though the agent\n"
            "   just did exactly that. WRONG. Choose `done`.\n"
            "2) Default to `continue` whenever the agent's claims are not backed\n"
            "   by concrete artifacts in the summary. Better to spend another\n"
            "   round verifying than declare premature `done`. The agent has no\n"
            "   ground-truth signal — your job is to demand evidence. But once\n"
            "   the evidence is in front of you (rule 1a), stop.\n"
            "2a) Acceptance-check failures override all self-report. If any\n"
            "   check in `Acceptance check results` is `[FAIL]`, choose\n"
            "   `continue` even if the main agent claims success. Your\n"
            "   `next_action` is the only repair prompt the engineer receives,\n"
            "   so include the failed command, exit code,\n"
            "   concrete issue codes/paths/messages, likely root cause, ordered\n"
            "   repair steps, and exact rerun command. Include full check output\n"
            "   so the engineer has all the context needed to fix the issue.\n"
            "   For `validate-*` commands, list every issue with its code, path,\n"
            "   and message, then provide concrete repair instructions.\n"
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
            "   success. No evidence → not done.\n"
            "8) Spec adherence: when the operator's request specifies CONCRETE\n"
            "   STRUCTURAL CONSTRAINTS — exact file paths, module/package\n"
            "   names, framework choice (e.g. `pytest` vs `unittest`),\n"
            "   API signatures, return-type contracts, count of test cases,\n"
            "   directory layout — the produced artifacts MUST match those\n"
            "   constraints unless the agent explicitly justified the\n"
            "   deviation in its summary AND the deviation is materially\n"
            "   equivalent. Any unjustified structural deviation → `continue`\n"
            "   with `next_action` naming the deviation (e.g. `the request\n"
            "   asked for tracker.py + pytest tests, but the agent built an\n"
            "   expense_tracker/ package using unittest. Either restructure\n"
            "   to match the spec or justify the deviation in the summary`).\n"
            "   Functional correctness alone is NOT sufficient when the\n"
            "   operator gave a precise structural contract. This rule\n"
            "   protects users who rely on exact paths/frameworks for\n"
            "   downstream tooling.\n"
            "9) Final-submission scope: ONLY when the Objective metadata says\n"
            "   `planner_scope: final_submission` or `Task scope: final_submission`,\n"
            "   choose `done` only if every item on the **full pipeline checklist**\n"
            "   injected above is satisfied (research → submission). A passing\n"
            "   single-stage checklist, a pilot run, or an underlength draft is NOT\n"
            "   enough for final submission. For positive paper objectives, do not\n"
            "   accept a negative-result pivot or a baseline-only win: the proposed\n"
            "   contribution must have a structured X-Y-Z-W paper_contribution claim\n"
            "   and beat the strongest nontrivial baseline on the declared metric\n"
            "   with statistical support. For `planner_scope: bounded` or absent\n"
            "   scope metadata, do not require the full pipeline checklist; judge\n"
            "   the bounded task by its own acceptance criteria and the relevant\n"
            "   per-stage checklist items.\n\n"
            f"Objective:\n{objective}\n\n"
            "Operator message history (source of truth for user instructions):\n"
            f"{operator_text}\n\n"
            "Planner guidance for this review:\n"
            f"{planner_review_instruction or 'none'}\n\n"
            f"Round: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n"
            f"{shared_context_block}"
            f"Main agent fatal error: {error_text}\n\n"
            "Main agent last summary:\n"
            f"{main_summary}\n\n"
            "Acceptance check results (include full details in next_action so engineer can act on them):\n"
            f"{check_text}\n"
            f"{evidence_block}"
        )


_MAX_SHARED_CTX_CHARS = 30000


def _format_engineer_shared_context(
    *,
    skill_used: str | None,
    engineer_reasoning_summary: str,
    prev_review_summary: str,
) -> str:
    """Render the read-only shared context block injected into reviewer prompts.

    Mirrors :func:`argus_skill.mission.reviewer._format_shared_context`
    so the two reviewer surfaces stay aligned.
    """
    skill = (skill_used or "").strip()
    reasoning = (engineer_reasoning_summary or "").strip()
    prev = (prev_review_summary or "").strip()
    if not skill and not reasoning and not prev:
        return ""
    parts = ["Shared read-only context (do NOT modify; advisory only):"]
    if skill:
        parts.append(f"- skill_used: {skill}")
    if reasoning:
        if len(reasoning) > _MAX_SHARED_CTX_CHARS:
            reasoning = reasoning[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in reasoning.splitlines())
        parts.append("- engineer_reasoning_summary:\n" + indented)
    if prev:
        if len(prev) > _MAX_SHARED_CTX_CHARS:
            prev = prev[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"


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
    completion_summary_markdown = _parse_optional_text(
        parsed.get("completion_summary_markdown")
    )
    if (
        confidence is None
        or reason is None
        or next_action is None
        or round_summary_markdown is None
        or completion_summary_markdown is None
    ):
        return None
    assert confidence is not None
    assert reason is not None
    assert next_action is not None
    assert round_summary_markdown is not None
    assert completion_summary_markdown is not None
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


def _parse_status(parsed: dict) -> ReviewStatus | None:
    for key in ("status", "decision", "action"):
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in {"done", "continue", "blocked"}:
            return cast(ReviewStatus, normalized)
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
            input_tokens=decision.input_tokens,
            output_tokens=decision.output_tokens,
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
